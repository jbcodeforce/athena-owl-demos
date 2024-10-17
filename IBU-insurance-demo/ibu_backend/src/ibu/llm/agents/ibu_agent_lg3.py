"""
Copyright 2024 Athena Decision Systems
@author Jerome Boyer
"""
import json
from fastapi.encoders import jsonable_encoder

import logging
from typing import Annotated, Any, Optional, Literal, List, Union
from typing_extensions import TypedDict
import langchain
from langchain_core.prompts import BasePromptTemplate
from langchain_core.messages import AnyMessage, ToolMessage, HumanMessage, AIMessage
from langchain_core.runnables.config import RunnableConfig

from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.pregel.types import StateSnapshot
from langgraph.prebuilt import ToolNode, tools_condition

from athena.routers.dto_models import StyledMessage

from athena.llm.agents.agent_mgr import OwlAgentDefaultRunner, OwlAgent, get_agent_manager
from athena.itg.store.content_mgr import get_content_mgr
from athena.llm.tools.tool_mgr import OwlToolEntity
from athena.routers.dto_models import ConversationControl, ResponseControl
from ibu.app_settings import get_config

from ibu.itg.ds.ComplaintHandling_generated_model import Motive, Claim
from pydantic import BaseModel
from ibu.itg.decisions.next_best_action_ds_client import callDecisionServiceWithClaim
from ibu.llm.tools.client_tools import build_or_get_instantiate_claim_repo


"""
An assistance to support the contact center agents from IBU insurance for the complaint management process.

The graph needs to do a query classification and routing to assess if the query is for complaint management or
for gathering information. Once in the complaint branch, the LLM should be able to identify the need
for tool calling and then let one of the Tool node performing the function execution.

Use memory to keep state of the conversation
"""  


"""
TODO:
- RAG only scenario must not call the DS and if possible, hallucinate (no mention to voucher)   ====> EXCEPTION
- DS only scenario must provide a clear recommendations with all the 4 steps and the explanations
- improve output content
- subsequent queries, e.g. what are the affiliated providers? => issue with API key on JC machine
- as a user, I want to know the nodes executed in the graph and why a particular was followed
- improve output style. we can use md and we could leverage: StyledMessage?

-----
- as a developer, I want a fine-grained view of the execution

"""

logging.basicConfig(
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S')
LOGGER = logging.getLogger(__name__)
if get_config().logging_level == "DEBUG":
    langchain.debug=True
    LOGGER.setLevel(logging.DEBUG)


""" motive
      - UnsatisfiedWithDelay: when the client is not happy with the time taken to process his claim. For instance, when he has not received any response to settle his claim several weeks after the claim was sent.
      - UnsatisfiedWithAppliedCoverages: when the client has received a settlement offer but is not satisfied because the coverages that he thought were applicable have not been applied, either partially (some coverages have not been applied) or totally (no coverage has been applied).
      - UnsatisfiedWithReimbursedAmount: when the client has received a settlement offer but is not satisfied with the proposed reimbursed amount.
      - UnsatisfiedWithQualityOfCustomerService
      """

class ComplaintInfo(BaseModel):
    claim_id: int
    motive: Motive
    intention_to_leave: bool

class AgentState(TypedDict):
    """
    Keep messages as chat history: message can be human, ai or tool messages.
    question asked by user, and documents retrieved from vector store
    """
    messages: Annotated[list[AnyMessage], add_messages]
    documents: List[str]
    complaint_info: Union[None, ComplaintInfo]
    claim: Union[None, Claim]
    #node_history: List[str]
    input: str
    path: str

def define_classifier_model():
    #return get_agent_manager().build_agent_runner("ibu_classify_query_agent","en")
    return get_agent_manager().build_agent_runner("ibu_classify_query_watson_agent","en")

def define_information_model():
    return get_agent_manager().build_agent_runner("ibu_information_agent","en")

def define_complaint_agent():
    agent_runner = get_agent_manager().build_agent_runner("ibu_complaint_agent2","en")
    return agent_runner.get_runnable().agent


def define_extract_info_agent():
    agent_runner = get_agent_manager().build_agent_runner("extract_info_to_decide","en")
    return agent_runner.get_runnable().agent

def format_docs(docs):
    list_messages = []  
    for doc in docs:
        list_messages.append(doc.page_content)
    return list_messages


def verbalize_en(motive: Motive) -> str:
    if motive == Motive.UnsatisfiedWithDelay:
        return 'is that the customer is unsatisfied with the delay of claim processing'
    elif motive == Motive.UnsatisfiedWithReimbursedAmount:
        return 'is that the customer is unsatisfied with the reimbursed amount'
    elif motive == Motive.UnsatisfiedWithAppliedCoverages:
        return 'is that the customer is unsatisfied with the applied coverages'
    elif motive == Motive.UnsatisfiedWithQualityOfCustomerService:
        return 'is that the customer if unsatisfied with the quality of customer service'
    elif motive == Motive.InformationInquiry:
        return 'is that the customer makes an information inquiry'
    else:
        return "has not been classified specifically."

class IBUInsuranceAgent(OwlAgentDefaultRunner):

    def __init__(self, agentEntity: OwlAgent, prompt: Optional[BasePromptTemplate], tool_instances: Optional[list[OwlToolEntity]]):
        self.agent_id = agentEntity.agent_id
        self.classifier_model= define_classifier_model()
        self.information_q_model = define_information_model() # TODO could comes for subagent list
        self.complaint_model = define_complaint_agent()
        self.extract_info_agent = define_extract_info_agent()
        self.tool_node=ToolNode(tool_instances)
        self.use_vector_store = False
        self.use_decision_svc = False
        self.rag_retriever= get_content_mgr().get_retriever()
        self.build_the_graph()
       

    def build_the_graph(self):
        graph_builder = StateGraph(AgentState)
        graph_builder.set_conditional_entry_point(
            self.process_classify_query,
            {
                "information": "gather_information",
                "complaint_with_decision": "extract_info",
                "complaint_with_no_decision": "process_complaint",
                "other": END
            },
        )
        graph_builder.add_node("gather_information", self.process_information_query)
        graph_builder.add_edge("gather_information", END)

        graph_builder.add_node("process_complaint", self.process_complaint)
        graph_builder.add_edge("process_complaint", END)

        graph_builder.add_node("extract_info", self.extract_info)
        graph_builder.add_node("call_decision_service", self.call_decision_service)
        graph_builder.add_edge("extract_info", "call_decision_service")
        graph_builder.add_edge("call_decision_service", END)

        self.checkpointer=MemorySaver()
        self.graph = graph_builder.compile(checkpointer=self.checkpointer)

   
    def process_classify_query(self, state: AgentState):
        messages = state['messages']
        question = state["input"].content
        #messages= [convert_message_to_dict(m) for m in messages]
        LOGGER.debug(f"\n@@@> {messages}")
        message = self.classifier_model.invoke({"input": [question], 
                                                "chat_history": messages}, 
                                                self.config["configurable"]["thread_id"])
        if "information" in message.lower():
            LOGGER.debug("---ROUTE QUESTION TO INFORMATION---")
            return "information"
        elif "complaint"  in message.lower():
            if self.use_decision_svc:
                LOGGER.debug("---ROUTE QUESTION TO Complaint handling with decision ---")
                return "complaint_with_decision"
            else:
                LOGGER.debug("---ROUTE QUESTION TO Complaint handling with no decision ---")
                return "complaint_with_no_decision"
        else:
            return "other"

    def process_information_query(self, state):
        LOGGER.info("--- graph.process_information_query")

        question = state["input"].content
        messages = state['messages']

        #uestion= messages[-1]
        if self.use_vector_store and self.rag_retriever:
            state["documents"] = self.rag_retriever.invoke(question)
        else:
            state["documents"] = []
        documents = state["documents"]

        LOGGER.info("BEFORE RAG in process_information_query")
        message = self.information_q_model.invoke({"input": [question], 
                                                   "context": format_docs(documents),
                                                   "chat_history": messages},
                                                    self.config["configurable"]["thread_id"]) # dict
        LOGGER.info("AFTER RAG in process_information_query")
        return {'messages': [AIMessage(content=message["output"])]}
    
    
    def process_complaint(self, state):  
        LOGGER.info("--- graph.process_complaint")
        messages = state['messages']
        question = state["input"].content
        if self.use_vector_store and self.rag_retriever:
            LOGGER.info("We have founded some documents")
            state["documents"] = self.rag_retriever.invoke(question)
        else:
            LOGGER.warning("We have not founded any documents for the RAG")
            state["documents"] = []
        documents = state["documents"]

        LOGGER.info("BEFORE RAG in process_complaint")
        LOGGER.info(f"question: {question}")
        LOGGER.info(f"format_docs(documents): {format_docs(documents)}")
        LOGGER.info(f"messages: {messages}")
        LOGGER.info(f"self.config['configurable']['thread_id']: {self.config['configurable']['thread_id']}")

        """
        messages = self.complaint_model.runnable.invoke({"input": [question],
                                               "context": format_docs(documents),
                                               "chat_history": messages,
                                                "intermediate_steps": [] }
                                               )
        """
        messages = self.complaint_model.runnable.invoke({
                                                    "input": [question], 
                                                    "context": format_docs(documents),
                                                    "chat_history": messages,
                                                    "intermediate_steps": [] 
                                                   },
                                                    self.config["configurable"]["thread_id"]) # dict
        
        LOGGER.info("AFTER RAG in process_complaint")        
        return {'messages': messages,'path': 'complaint'}
    
    def extract_info(self, state):  
        LOGGER.warning("--- Extracting information ---")

        messages = state['messages']
        question = state["input"].content
        documents = []
        result = self.extract_info_agent.runnable.invoke({"input": [question],
                                               "context": format_docs(documents),
                                               "chat_history": messages,
                                                "intermediate_steps": [] }
                                               )

        # TODO: wish for the Athena Agent Framework
        # Creating an instance of a ComplaintInfo object could be the job of a PydanticOutputParser
        # As a developer, I want to be able to specify an output parser for an agent declaratively by adding a line in the agents.yaml file.
        output_elements = result.return_values['output'].split(',')
        motive = output_elements[0]
        intention_to_leave = output_elements[1] == "True"
        claim_id: int = int(output_elements[2])

        complaint_info = ComplaintInfo(claim_id =claim_id, 
                                       motive = motive, 
                                       intention_to_leave = intention_to_leave)
        LOGGER.warning(complaint_info)
        return {
            "complaint_info": complaint_info
        }
    
    def call_decision_service(self, state):  
        LOGGER.info(f"--- Calling the decision service with claim {state['complaint_info'].claim_id} ---")

        config = get_config()
        claim_data_repo = build_or_get_instantiate_claim_repo()

        load_claim = False
        print(state)
        if 'claim' in state.keys() and state['claim'] != None:
            LOGGER.info(f"---- claim {state['complaint_info'].claim_id} is already in the LG context")
            claim = state['claim']
        else:
            LOGGER.info(f"---- claim {state['complaint_info'].claim_id} is loaded from the backend data APIs")
            claim = claim_data_repo.get_claim(state['complaint_info'].claim_id)   
            load_claim = True
    
        result = callDecisionServiceWithClaim(config, claim, state['complaint_info'].motive, state['complaint_info'].intention_to_leave, "en")

        LOGGER.info("-------------")
        LOGGER.info(f"claim type: {type(claim)}")
        LOGGER.info(f"result: {result}")

        claim_json_data = jsonable_encoder(claim)

        step1 = f"STEP 1: the reason for the incoming communication {verbalize_en(state['complaint_info'].motive)}.\n\n"
        step2 = f"STEP 2: the customer has shown {'some' if state['complaint_info'].intention_to_leave else 'no'} intention to leave.\n\n"
        step3 = f"STEP 3: the claim id used as reference is #{state['complaint_info'].claim_id}.\n {json.dumps(claim_json_data, indent=4)}\n\n"

        if load_claim:
            return {
                'messages': step1 + step2 + step3 + "STEP 4: " + result,
                'claim': claim
            }
        else:
            return {
                'messages': step1 + step2 + step3 + "STEP 4: " + result
            }



    # ==================== overrides =============================
    def invoke(self, request, thread_id: Optional[str], **kwargs) -> dict[str, Any] | Any:
        if kwargs["vector_store"]:
            self.use_vector_store = True
        if kwargs["decision_svc"]:
            self.use_decision_svc = True
        self.config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        user_input=HumanMessage(content=request)
        resp= self.graph.invoke({"messages": [user_input], "input": user_input}, self.config)
        msg=resp["messages"][-1].content
        return msg
    
    def get_state(self) -> StateSnapshot:
        return self.graph.get_state(self.config)
    
    def send_conversation(self, controller: ConversationControl) -> ResponseControl | Any:
        """
        Override the default as the history is kept in the graph state
        and we need to take into account the tools needed dynamically
        There is no need to process closed questions too
        """ 
        agent_resp= self.invoke(controller.query, 
                                controller.thread_id, 
                                vector_store = controller.callWithVectorStore,
                                decision_svc = controller.callWithDecisionService)   # AIMessage
        resp = self.build_response(controller,agent_resp)
        return resp
    