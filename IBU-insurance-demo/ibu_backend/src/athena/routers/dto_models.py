"""
Copyright 2024 Athena Decision Systems
@author Jerome Boyer
"""
from pydantic import BaseModel
from typing import List, Optional
 

    
class ChatRecord(BaseModel):
    role: str
    content: str
    
class ModelParameters(BaseModel):
    modelName: str = ""
    modelClass: str = "agent_openai"
    temperature: int = 0
    top_k: int = 1
    top_p: int = 1
    
    
class ConversationControl(BaseModel):
    callWithVectorStore: bool = False
    callWithDecisionService: bool = False
    locale: str = "en"
    query: str = ""
    type: str = "chat"
    
    prompt_ref:  str = "openai_insurance_with_tool"
    modelParameters: Optional[ModelParameters] = None
    chat_history: List[ChatRecord] = []


class ResponseChoice(BaseModel):
    choice: str = ""

class ResponseControl(BaseModel):
    message: Optional[str] = ''
    status: int = 200
    type: str ="OpenQuestion"
    question: Optional[str] = ''
    question_type: Optional[str] = ''
    possibleResponse: Optional[List[ResponseChoice]] = None
    error: Optional[str] = ''


