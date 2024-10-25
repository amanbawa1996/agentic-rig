# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os

from typing_extensions import TypedDict
from typing import List
import data_gemma as dg
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
import torch
from langchain.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langchain_community.tools.tavily_search import TavilySearchResults
import requests
from chatui.utils import database, nim

DC_API_KEY = os.environ['DC_API_KEY']
HF_TOKEN = os.environ['HF_TOKEN']
#HF_API_KEY = os.environ.get("HF_API_KEY")
HF_API_ENDPOINT = os.environ["HF_API_ENDPOINT"]  # Add your custom endpoint here

# Initialize Data Commons API client
dc = dg.DataCommons(api_key=DC_API_KEY)

# Define the local model path (where the model is stored in your GitHub repo after uploading)
model_name_or_path = "../models"  # Adjust this path based on where you store the model

# Check if CUDA (GPU) is available for faster inference; otherwise, use CPU or fallback to API
device = "cuda" if torch.cuda.is_available() else "cpu"
use_local_model = torch.cuda.is_available()

# If local model should be used, load it
if use_local_model:

        nf4_config = BitsAndBytesConfig(
           load_in_4bit=True,
           bnb_4bit_quant_type="nf4",
           bnb_4bit_compute_dtype=torch.bfloat16
        )


        # If local loading fails, fallback to Hugging Face model download
        model_name = 'google/datagemma-rig-27b-it'
        tokenizer = AutoTokenizer.from_pretrained(model_name, token=HF_TOKEN)
        datagemma_model = AutoModelForCausalLM.from_pretrained(
            model_name,
            quantization_config=nf4_config,  # Use GPU if available, otherwise CPU
            torch_dtype=torch.bfloat16,  # Use appropriate precision
            token=HF_TOKEN
        )
        print(f"Model successfully loaded from Hugging Face: {model_name}")
### State


class GraphState(TypedDict):
    """
    Represents the state of our graph.

    Attributes:
        question: question
        generation: LLM generation
        web_search: whether to add search
        documents: list of documents
    """

    question: str
    generation: str
    web_search: str
    documents: List[str]
    generator_model_id: str
    router_model_id: str
    retrieval_model_id: str
    hallucination_model_id: str
    answer_model_id: str
    prompt_generator: str
    prompt_router: str
    prompt_retrieval: str
    prompt_hallucination: str
    prompt_answer: str
    router_use_nim: bool
    retrieval_use_nim: bool
    generator_use_nim: bool
    hallucination_use_nim: bool
    answer_use_nim: bool
    nim_generator_ip: str
    nim_router_ip: str
    nim_retrieval_ip: str
    nim_hallucination_ip: str
    nim_answer_ip: str
    nim_generator_port: str
    nim_router_port: str
    nim_retrieval_port: str
    nim_hallucination_port: str
    nim_answer_port: str
    nim_generator_id: str
    nim_router_id: str
    nim_retrieval_id: str
    nim_hallucination_id: str
    nim_answer_id: str


from langchain.schema import Document

### Nodes



def retrieve(state):
    """
    Retrieve documents from vectorstore

    Args:
        state (dict): The current graph state

    Returns:
        state (dict): New key added to state, documents, that contains retrieved documents
    """
    print("---RETRIEVE---")
    question = state["question"]

    # Retrieval
    retriever = database.get_retriever()
    documents = retriever.invoke(question)
    return {"documents": documents, "question": question}


def generate(state):
    """
    Generate an answer using NVIDIA NIMs, the local model (with GPU or CPU), or the Hugging Face Inference API.

    Args:
        state (dict): The current graph state

    Returns:
        state (dict): New key added to state, generation, that contains LLM generation
    """
    question = state["question"]
    documents = state["documents"]

    # Prepare documents for the prompt
    retrieved_content = "\n".join([doc.page_content for doc in documents])
    
    # Define the interleaved prompt template
    interleaved_prompt_template = """
    Question: {question}
    
    You have access to the following documents:
    {document}
    
    Please provide a response that leverages both the retrieved information and generation. 
    Interleave your response with relevant portions of the documents to support your answer.

    Answer:
    """
    
    # Create a PromptTemplate object
    prompt = PromptTemplate(
        template=interleaved_prompt_template,
        input_variables=["question", "document"]
    )
    
    # NVIDIA NIMs logic
    if state.get("generator_use_nim", False):
        print("---GENERATE WITH NVIDIA NIMs---")
    
        # llm = nim.CustomChatOpenAI(
        #     custom_endpoint=state["nim_generator_ip"], 
        #     port=state["nim_generator_port"] if len(state["nim_generator_port"]) > 0 else "8000",
        #     model_name=state["nim_generator_id"] if len(state["nim_generator_id"]) > 0 else "meta/llama3-8b-instruct",
        #     temperature=0.7
        # )
        llm = ChatNVIDIA(model=state["generator_model_id"], temperature=0.7)
        
        # Chain the prompt template with the LLM and StrOutputParser
        rag_chain = prompt | llm | StrOutputParser()
        
        # Invoke the chain with the inputs
        generation = rag_chain.invoke({"question": question, "document": retrieved_content})
        

        
    # If using the local model (either GPU or CPU)
    else:
        prompt_template = interleaved_prompt_template.format(question=question, document=retrieved_content)
        # Define the maximum allowed input length for Hugging Face
        max_input_length = 1512 - 150  # Leave space for 150 generated tokens
        
        # If input length exceeds max_input_length, truncate the prompt_template
        if len(prompt_template) > max_input_length:
            prompt_template = prompt_template[:max_input_length]
            
        if use_local_model:
            print("---GENERATE WITH LOCAL MODEL (CUDA or CPU)---")
            
            # Tokenize the input for model inference
            inputs = tokenizer(prompt_template, return_tensors="pt").to(device)
    
            # Generate the response with the interleaved input
            with torch.no_grad():
                outputs = datagemma_model.generate(
                    inputs["input_ids"],
                    max_new_tokens=150,
                    do_sample=True,
                    temperature=0.7,
                    top_p=0.9,
                    num_return_sequences=1,
                )
    
            # Decode the generated response
            generation = tokenizer.decode(outputs[0], skip_special_tokens=True)
    
        # If CUDA is not available, use the Hugging Face Inference API
        else:
            
            
            # Proceed with the Hugging Face API call
            print("---GENERATE WITH HUGGING FACE API---")
            
            if not HF_TOKEN:
                raise ValueError("Hugging Face API key is missing. Please set the HF_TOKEN environment variable.")
                        
            headers = {"Authorization": f"Bearer {HF_TOKEN}"}
            payload = {
                "inputs": prompt_template,
                "parameters": {
                    "max_new_tokens": 150,
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "do_sample": True
                }
            }
            
            response = requests.post(HF_API_ENDPOINT, headers=headers, json=payload)
            
            if response.status_code == 200:
                generated_text = response.json()[0]["generated_text"]
                generation = generated_text.split("Answer:")[-1].strip()  # Extract the portion after "Answer:"
                
            else:
                error_msg = f"Error: {response.status_code}, {response.text}"
                print(error_msg)
                raise Exception(error_msg)

    # Return the generation and documents
    return {
        "documents": documents,
        "question": question,
        "generation": generation
    }

def grade_documents(state):
    """
    Determines whether the retrieved documents are relevant to the question
    If any document is not relevant, we will set a flag to run web search

    Args:
        state (dict): The current graph state

    Returns:
        state (dict): Filtered out irrelevant documents and updated web_search state
    """

    print("---CHECK DOCUMENT RELEVANCE TO QUESTION---")
    question = state["question"]
    documents = state["documents"]

    # Score each doc
    filtered_docs = []
    web_search = "No"
    prompt = PromptTemplate(
        template=state["prompt_retrieval"],
        input_variables=["question", "document"],
    )
    llm = nim.CustomChatOpenAI(custom_endpoint=state["nim_retrieval_ip"], 
                               port=state["nim_retrieval_port"] if len(state["nim_retrieval_port"]) > 0 else "8000",
                               model_name=state["nim_retrieval_id"] if len(state["nim_retrieval_id"]) > 0 else "meta/llama3-8b-instruct",
                               temperature=0.7) if state["retrieval_use_nim"] else ChatNVIDIA(model=state["retrieval_model_id"], temperature=0)
    retrieval_grader = prompt | llm | JsonOutputParser()
    for d in documents:
        score = retrieval_grader.invoke(
            {"question": question, "document": d.page_content}
        )
        grade = score["score"]
        # Document relevant
        if grade.lower() == "yes":
            print("---GRADE: DOCUMENT RELEVANT---")
            filtered_docs.append(d)
        # Document not relevant
        else:
            print("---GRADE: DOCUMENT NOT RELEVANT---")
            # We do not include the document in filtered_docs
            continue
    # We set a flag to indicate that we want to run web search if insufficient relevant docs found
    web_search = "Yes" if len(filtered_docs) < 1 else "No"
    return {"documents": filtered_docs, "question": question, "web_search": web_search}


def web_search(state):
    """
    Web search based based on the question

    Args:
        state (dict): The current graph state

    Returns:
        state (dict): Appended web results to documents
    """

    print("---WEB SEARCH---")
    question = state["question"]
    documents = state["documents"]

    # Web search
    web_search_tool = TavilySearchResults(k=3)
    docs = web_search_tool.invoke({"query": question})
    web_results = "\n".join([d["content"] for d in docs])
    web_results = Document(page_content=web_results)
    if documents is not None:
        documents.append(web_results)
    else:
        documents = [web_results]
    return {"documents": documents, "question": question}


### Conditional edge


def route_question(state):
    """
    Route question to web search or RAG.

    Args:
        state (dict): The current graph state

    Returns:
        str: Next node to call
    """

    print("---ROUTE QUESTION---")
    question = state["question"]
    print(question)
    prompt = PromptTemplate(
        template=state["prompt_router"],
        input_variables=["question"],
    )
    llm = nim.CustomChatOpenAI(custom_endpoint=state["nim_router_ip"], 
                               port=state["nim_router_port"] if len(state["nim_router_port"]) > 0 else "8000",
                               model_name=state["nim_router_id"] if len(state["nim_router_id"]) > 0 else "meta/llama3-8b-instruct",
                               temperature=0.7) if state["router_use_nim"] else ChatNVIDIA(model=state["router_model_id"], temperature=0)
    question_router = prompt | llm | JsonOutputParser()
    source = question_router.invoke({"question": question})
    print(source)
    if source["datasource"] == "web_search":
        print("---ROUTE QUESTION TO WEB SEARCH---")
        return "websearch"
    elif source["datasource"] == "vectorstore":
        print("---ROUTE QUESTION TO RAG---")
        return "vectorstore"


def decide_to_generate(state):
    """
    Determines whether to generate an answer, or add web search

    Args:
        state (dict): The current graph state

    Returns:
        str: Binary decision for next node to call
    """

    print("---ASSESS GRADED DOCUMENTS---")
    question = state["question"]
    web_search = state["web_search"]
    filtered_documents = state["documents"]

    if web_search == "Yes":
        # All documents have been filtered check_relevance
        # We will re-generate a new query
        print(
            "---DECISION: ALL DOCUMENTS ARE NOT RELEVANT TO QUESTION, INCLUDE WEB SEARCH---"
        )
        return "websearch"
    else:
        # We have relevant documents, so generate answer
        print("---DECISION: GENERATE---")
        return "generate"


### Conditional edge


def grade_generation_v_documents_and_question(state):
    """
    Determines whether the generation is grounded in the document and answers question.

    Args:
        state (dict): The current graph state

    Returns:
        str: Decision for next node to call
    """

    print("---CHECK HALLUCINATIONS---")
    question = state["question"]
    documents = state["documents"]
    generation = state["generation"]

    prompt = PromptTemplate(
        template=state["prompt_hallucination"],
        input_variables=["generation", "documents"],
    )
    llm = nim.CustomChatOpenAI(custom_endpoint=state["nim_hallucination_ip"], 
                               port=state["nim_hallucination_port"] if len(state["nim_hallucination_port"]) > 0 else "8000",
                               model_name=state["nim_hallucination_id"] if len(state["nim_hallucination_id"]) > 0 else "meta/llama3-8b-instruct",
                               temperature=0.7) if state["hallucination_use_nim"] else ChatNVIDIA(model=state["hallucination_model_id"], temperature=0)
    hallucination_grader = prompt | llm | JsonOutputParser()

    score = hallucination_grader.invoke(
        {"documents": documents, "generation": generation}
    )
    grade = score["score"]

    # Check hallucination
    prompt = PromptTemplate(
        template=state["prompt_answer"],
        input_variables=["generation", "question"],
    )
    llm = nim.CustomChatOpenAI(custom_endpoint=state["nim_answer_ip"], 
                               port=state["nim_answer_port"] if len(state["nim_answer_port"]) > 0 else "8000",
                               model_name=state["nim_answer_id"] if len(state["nim_answer_id"]) > 0 else "meta/llama3-8b-instruct",
                               temperature=0.7) if state["answer_use_nim"] else ChatNVIDIA(model=state["answer_model_id"], temperature=0)
    answer_grader = prompt | llm | JsonOutputParser()
    
    if grade == "yes":
        print("---DECISION: GENERATION IS GROUNDED IN DOCUMENTS---")
        # Check question-answering
        print("---GRADE GENERATION vs QUESTION---")
        score = answer_grader.invoke({"question": question, "generation": generation})
        grade = score["score"]
        if grade == "yes":
            print("---DECISION: GENERATION ADDRESSES QUESTION---")
            return "useful"
        else:
            print("---DECISION: GENERATION DOES NOT ADDRESS QUESTION---")
            return "not useful"
    else:
        print("---DECISION: GENERATION IS NOT GROUNDED IN DOCUMENTS, RE-TRY---")
        return "not supported"