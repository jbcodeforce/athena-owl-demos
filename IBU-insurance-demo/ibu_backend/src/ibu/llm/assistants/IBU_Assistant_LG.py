"""
Copyright 2024 Athena Decision Systems
@author Jerome Boyer
"""
from typing_extensions import TypedDict
from typing import Annotated, Literal, Any, Optional, List
from langchain_core.pydantic_v1 import BaseModel, Field
import json,logging

from athena.app_settings import get_config
from athena.llm.assistants.assistant_mgr import OwlAssistant
from athena.llm.agents.agent_mgr import get_agent_manager, OwlAgentEntity, OwlAgentInterface
from athena.routers.dto_models import ConversationControl, ResponseControl
from athena.itg.store.content_mgr import get_content_mgr
 
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.runnables.config import RunnableConfig
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.prompts import ChatPromptTemplate
from langchain.schema import Document
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langgraph.pregel.types import StateSnapshot
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph, START, END

import langchain

LOGGER = logging.getLogger(__name__)
logging.basicConfig(
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S')

if get_config().logging_level == "DEBUG":
    langchain.debug=True
  
"""
An assistance to support the contact center agents from IBU insurance for the complaint management process.

The graph needs to do a query classification and routing to assess if the query is for complaint management or
for gathering information. Once in the complaint branch, the LLM should be able to identify the need
for tool calling and then let one of the Tool node performing the function execution.

Use memory to keep state of the conversation
"""  

class AgentState(TypedDict):
    """
    Keep messages as chat history: message can be human, ai or tool messages.
    question asked by user, and documents retrieved from vector store
    """
    messages: Annotated[list[AnyMessage], add_messages]
    documents: List[str]
    question: str

    

class BasicToolNode:
    """A node that runs the tools requested in the last AIMessage."""
    def __init__(self, tools: list) -> None:
        # list of tools of type langchain basetool
        self.tools_by_name = {tool.name: tool for tool in tools}
        LOGGER.debug(f"\n@@@> tools in node: {self.tools_by_name}")

    def __call__(self, inputs: dict):
        # Perform the tool calling if the last message has tool calls list.
        if messages := inputs.get("messages", []):
            last_message = messages[-1]
        else:
            raise ValueError("No message found in input")
        outputs = []   # keep outputs of all the tool calls
        for tool_call in last_message.tool_calls:
            tool_result = self.tools_by_name[tool_call["name"]].invoke(
                tool_call["args"]
            )
            outputs.append(
                ToolMessage(
                    content=json.dumps(tool_result),
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"],
                )
            )
        return {"messages": outputs}


    
def get_or_build_classifier_agent() -> OwlAgentInterface | None:
    # This method is temporary - need to get assistant supporting multiple agents
    mgr = get_agent_manager()
    oae: Optional[OwlAgentEntity] = mgr.get_agent_by_id("ibu_classify_query_agent")
    if oae is None:
        raise ValueError("ibu_classify_query_agent agent not found")
    return mgr.build_agent(oae.agent_id,"en")

 
def define_information_q_model() ->OwlAgentInterface | None:
    # This method is temporary - need to get assistant supporting multiple agents
    
    mgr = get_agent_manager()
    oae: Optional[OwlAgentEntity] = mgr.get_agent_by_id("ibu_tool_rag_agent_limited")
    if oae is None:
        raise ValueError("ibu_tool_rag_agent_limited agent not found")
    return mgr.build_agent(oae.agent_id,"en")

def web_search(state):
    print("---WEB SEARCH---")
    question = state["question"]
    web_search_tool = TavilySearchResults(k=3)
    docs = web_search_tool.invoke({"query": question})
    web_results = "\n".join([d["content"] for d in docs])
    web_results = Document(page_content=web_results)
    return {"documents": web_results, "question": question}
     
class IBUInsuranceAssistant(OwlAssistant):
    
    def __init__(self,agent,assistantID):
        super().__init__(assistantID)
        self.memory = SqliteSaver.from_conn_string(":memory:")
        self.classifier_model: Optional[OwlAgentInterface] = get_or_build_classifier_agent()
        self.information_q_model: Optional[OwlAgentInterface] = define_information_q_model()
        self.model = agent.get_runnable()
        self.use_vector_store = False
        self.prompt = agent.get_prompt()
        self.rag_retriever = get_content_mgr().get_retriever()
        
        tools = agent.get_tools() + self.information_q_model.get_tools()
        tool_node=BasicToolNode(tools) 
        # ================= DEFINE GRAPH ====================
        graph = StateGraph(AgentState)
        graph.add_node("information_node", self.process_information_query)
        graph.add_node("complaint", self.process_complaint)
        graph.add_node("tools", tool_node)
        graph.set_conditional_entry_point(
            self.process_classify_query,
            {
                "information": "information_node",
                "complaint": "complaint",
            },
        )
        
        graph.add_conditional_edges(
            "information_node",
            self.route_tools,
            { 
             "tools": "tools", 
             END: END}
        )
        graph.add_edge("tools", "information_node")
        graph.add_conditional_edges(
            "complaint",
            self.route_tools,
            { 
             "tools": "tools", 
             END: END}
        )
        graph.add_edge("tools", "complaint")

        self.graph = graph.compile(checkpointer=self.memory)


    def process_classify_query(self, state: AgentState):
        messages = state['messages']
        question = state["question"]
        #messages= [convert_message_to_dict(m) for m in messages]
        LOGGER.debug(f"\n@@@> {messages}")
        message = self.classifier_model.invoke({"question": question})
        if message.next_task == "information":
            print("---ROUTE QUESTION TO INFORMATION---")
            return "information"
        elif message.next_task == "complaint":
            print("---ROUTE QUESTION TO Complaint---")
            return "complaint"


    def process_information_query(self, state):
        question = state["question"]
        if self.use_vector_store:
            state["documents"] = self.rag_retriever.invoke(question)
        documents = state["documents"]

        message = self.information_q_model.invoke({"question": question, "context": documents}) # dict
        return {'messages': [AIMessage(content=message["output"])], "question" : message["question"]}
    
    def process_complaint(self, state):  
        messages = state['messages']
        question = state["question"]
        if self.use_vector_store:
            state["documents"] = self.rag_retriever.invoke(question)
        else:
            state["documents"] = []
        documents = state["documents"]
        LOGGER.debug(f"\n@@@> {messages}")
        message = self.model.invoke({"question": question, "context": documents})
        return {'messages': [AIMessage(content=message["output"])]}
    
   
    def route_tools(self,
        state: AgentState,
    ) -> Literal["tools", END]:
        """Use in the conditional_edge to route to the ToolNode if the last message
        has tool calls. Otherwise, route to the end."""
        if messages := state.get("messages", []):
            ai_message = messages[-1]
            LOGGER.debug(f"\n@@@> {ai_message}")
        else:
            raise ValueError(f"No messages found in input state to tool_edge: {state}")
        
        if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
            return "tools"
        return END
    
    def invoke(self, request, thread_id: Optional[str], **kwargs) -> dict[str, Any] | Any:
        if kwargs["vector_store"]:
            self.use_vector_store = True
        self.config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

        resp= self.graph.invoke(request, self.config)
        msg=resp["messages"][-1].content
        return msg
    
    def get_state(self) -> StateSnapshot:
        return self.graph.get_state(self.config)

    
    def send_conversation(self, controller: ConversationControl) -> ResponseControl | Any:
         # overwrite the default. 
        request = { "question": controller.query }
        agent_resp= self.invoke(request, controller.thread_id, vector_store = controller.callWithVectorStore)   # AIMessage
        resp = self._build_response(controller)
        resp.message=agent_resp
        return resp