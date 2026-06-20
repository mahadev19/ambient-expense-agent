import os
import json
import asyncio
from pathlib import Path

# Load env variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import google.auth
from google.auth.exceptions import DefaultCredentialsError

# Configure authentication based on .env / environment settings
if os.environ.get("GOOGLE_API_KEY"):
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "False"
else:
    try:
        _, project_id = google.auth.default()
        if project_id:
            os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
    except DefaultCredentialsError:
        pass

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.events.request_input import RequestInput
from google.genai import types

from expense_agent.agent import root_agent

def is_interrupt(event) -> bool:
    """Helper to detect if an event represents a human-in-the-loop interrupt."""
    if event.content and event.content.parts:
        for part in event.content.parts:
            if part.function_call and part.function_call.name == "adk_request_input":
                return True
    return False

async def run_scenario(runner, payload_json: str, case_id: str):
    message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=payload_json)]
    )
    
    session_id = f"eval_{case_id}"
    user_id = "eval_user"
    
    events = []
    has_interrupt = False
    
    # First execution pass
    async for event in runner.run_async(
        new_message=message,
        user_id=user_id,
        session_id=session_id
    ):
        events.append(event)
        if is_interrupt(event):
            has_interrupt = True
            
    # Resume pass if workflow paused at the human approval gate
    if has_interrupt:
        payload = json.loads(payload_json)
        desc = payload.get("description", "").lower()
        
        # Rule-based decision for Human Approval:
        # - Reject prompt injections (description contains "ignore" or "bypass")
        # - Reject case 'high_value_clean_reject'
        # - Approve clean requests
        if "ignore" in desc or "bypass" in desc or "reject" in case_id:
            decision = "Rejected"
        else:
            decision = "Approved"
            
        print(f"[{case_id}] Workflow paused at human gate. Resuming with automated decision: {decision}")
        
        # Find invocation_id from events
        invocation_id = None
        for event in reversed(events):
            if getattr(event, "invocation_id", None):
                invocation_id = event.invocation_id
                break
        
        # Build the FunctionResponse to resume
        part = types.Part(
            function_response=types.FunctionResponse(
                id="human_decision",
                name="adk_request_input",
                response={"result": decision}
            )
        )
        resume_message = types.Content(
            role="user",
            parts=[part]
        )
        
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            invocation_id=invocation_id,
            new_message=resume_message
        ):
            events.append(event)
            
    return events

async def main():
    dataset_path = Path("tests/eval/datasets/basic-dataset.json")
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)
        
    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        session_service=session_service,
        app_name="expense_agent",
        auto_create_session=True,
    )
    
    eval_cases = []
    
    for case in dataset["eval_cases"]:
        case_id = case["eval_case_id"]
        prompt_text = case["prompt"]["parts"][0]["text"]
        
        print(f"Running scenario: {case_id}")
        events = await run_scenario(runner, prompt_text, case_id)
        
        # Extract the final outcome from events
        final_outcome = None
        for event in reversed(events):
            if not is_interrupt(event) and event.output:
                final_outcome = event.output
                break
                
        # Format turns for AgentData
        turns = []
        turn0_events = [
            {
                "author": "user",
                "content": {
                    "role": "user",
                    "parts": [{"text": prompt_text}]
                }
            }
        ]
        
        for event in events:
            if is_interrupt(event):
                msg = ""
                for part in event.content.parts:
                    if part.function_call and part.function_call.name == "adk_request_input":
                        msg = part.function_call.args.get("message", "")
                        break
                turn0_events.append({
                    "author": "expense_agent",
                    "content": {
                        "role": "model",
                        "parts": [{"text": f"RequestInput Interrupt message: {msg}"}]
                    }
                })
            else:
                output_str = json.dumps(event.output) if event.output else ""
                turn0_events.append({
                    "author": "expense_agent",
                    "content": {
                        "role": "model",
                        "parts": [{"text": f"Node execution output: {output_str}"}]
                    }
                })
                
        turns.append({
            "turn_index": 0,
            "events": turn0_events
        })
        
        # Build EvalCase matching canonical grading schema
        response_text = json.dumps(final_outcome) if final_outcome else "No final outcome"
        eval_case = {
            "eval_case_id": case_id,
            "prompt": {
                "role": "user",
                "parts": [{"text": prompt_text}]
            },
            "responses": [
                {
                    "response": {
                        "role": "model",
                        "parts": [{"text": response_text}]
                    }
                }
            ],
            "agent_data": {
                "agents": {
                    "expense_agent": {
                        "agent_id": "expense_agent",
                        "instruction": "Ambient Expense Approval Agent"
                    }
                },
                "turns": turns
            }
        }
        eval_cases.append(eval_case)
        
    # Write outputs
    output_path = Path("artifacts/traces/generated_traces.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"eval_cases": eval_cases}, f, indent=2)
        
    print(f"Traces written successfully to: {output_path}")

if __name__ == "__main__":
    asyncio.run(main())
