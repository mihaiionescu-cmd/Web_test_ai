import asyncio
import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

import asyncpg
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from browser_use.llm import ChatAzureOpenAI, ChatOllama
from browser_use import Agent, Tools, ActionResult, Browser
from allure_reporter import AllureReporter

from database import init_database, generate_session_id, get_session_summary, get_all_sessions_data, DATABASE_URL

load_dotenv()

tools = Tools()
app = FastAPI(title="Test Automation API", version="1.0.0")
allure_reporter = AllureReporter("/app/allure-results", "/app/allure-reports")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class GenerateTestCasesRequest(BaseModel):
    url: str
    num_test_cases: int

class TestCaseResponse(BaseModel):
    id: int
    test_id: int
    title: str
    description: Optional[str]
    steps: str
    status: str
    comment: Optional[str]
    executed_at: Optional[datetime]

class SessionResponse(BaseModel):
    session_id: str
    url: str
    num_test_cases: int
    created_at: datetime
    status: str
    test_cases: List[TestCaseResponse] = []

class SessionSummaryResponse(BaseModel):
    session: Dict[str, Any]
    stats: Dict[str, int]
    test_cases: List[Dict[str, Any]]

@tools.action('Create Test Session')
async def create_test_session(session_id: str, url: str, num_test_cases: int) -> ActionResult:
    try:
        pool: asyncpg.Pool = app.state.pool
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO sessions (session_id, url, num_test_cases) VALUES ($1, $2, $3)",
                session_id, url, num_test_cases
            )
        return ActionResult(extracted_content=f"Created session {session_id}")
    except Exception as e:
        return ActionResult(error=str(e))

@tools.action('Save Test Cases')
async def save_test_cases(session_id: str, test_cases_json: str) -> ActionResult:
    try:
        data = json.loads(test_cases_json) if isinstance(test_cases_json, str) else test_cases_json
        test_cases = data if isinstance(data, list) else data.get("test_cases", [])

        pool: asyncpg.Pool = app.state.pool
        async with pool.acquire() as conn:
            for case in test_cases:
                steps = case.get("steps", [])
                steps_str = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps)) if isinstance(steps, list) else str(steps)
                await conn.execute("""
                    INSERT INTO test_cases (session_id, test_id, title, description, steps)
                    VALUES ($1, $2, $3, $4, $5)
                """, session_id, int(case.get("id", 0)), case.get("title", "Untitled Test"),
                                   case.get("description", ""), steps_str)
        return ActionResult(extracted_content=f"Saved {len(test_cases)} test cases")
    except Exception as e:
        return ActionResult(error=str(e))

@tools.action('Update Test Case Status')
async def update_test_case_status(session_id: str, test_id: int, status: str, comment: str = "") -> ActionResult:
    try:
        pool: asyncpg.Pool = app.state.pool
        async with pool.acquire() as conn:
            result = await conn.fetchrow(
                "SELECT id, title FROM test_cases WHERE session_id = $1 AND test_id = $2",
                session_id, test_id
            )
            if not result:
                return ActionResult(error=f"Test case {test_id} not found")

            await conn.execute("""
                UPDATE test_cases 
                SET status = $1, comment = $2, executed_at = CURRENT_TIMESTAMP
                WHERE session_id = $3 AND test_id = $4
            """, status, comment, session_id, test_id)

        return ActionResult(
            extracted_content=f"Updated Test {test_id}",
            is_done=True,
            success=True
        )
    except Exception as e:
        return ActionResult(error=str(e))

@tools.action('Complete Test Session')
async def complete_test_session(session_id: str) -> ActionResult:
    try:
        pool: asyncpg.Pool = app.state.pool
        async with pool.acquire() as conn:
            await conn.execute("UPDATE sessions SET status = 'Completed' WHERE session_id = $1", session_id)
        return ActionResult(extracted_content=f"Session {session_id} completed")
    except Exception as e:
        return ActionResult(error=str(e))

async def create_test_plan(url: str, num_cases: int, session_id: str) -> str:
    llm = ChatAzureOpenAI(model="gpt-4.1")
    #llm = ChatOllama(model="llama3.1:8b")

    task_create_session = f"""
    Call the 'Create Test Session' tool with:
    - session_id: "{session_id}"
    - url: "{url}"
    - num_test_cases: {num_cases}
    """

    agent = Agent(task=task_create_session, llm=llm, tools=tools, max_actions_per_step=1,
                  browser=Browser(headless=True, window_size={'width': 1920, 'height': 1080}))
    await agent.run()
    print(f"Session created: {session_id}")

    task_generate_tests = f"""
    Generate {num_cases} test cases for {url}.
    Call 'Save Test Cases' with session_id "{session_id}" and the JSON array of cases.
    """

    agent = Agent(task=task_generate_tests, llm=llm, tools=tools, max_actions_per_step=1,
                  browser=Browser(headless=True, window_size={'width': 1920, 'height': 1080}))
    await agent.run()

    print(f"Test cases saved for {session_id}")
    return session_id

async def execute_test_plan(session_id: str):
    pool: asyncpg.Pool = app.state.pool

    async with pool.acquire() as conn:
        session_data = await conn.fetchrow("SELECT url, num_test_cases FROM sessions WHERE session_id = $1", session_id)

    if not session_data:
        print(f"Session {session_id} not found")
        return

    url, num_cases = session_data['url'], session_data['num_test_cases']

    async with pool.acquire() as conn:
        test_cases = await conn.fetch(
            "SELECT test_id, title, description, steps FROM test_cases WHERE session_id = $1 ORDER BY test_id",
            session_id
        )

    if not test_cases:
        print(f"No test cases for session {session_id}")
        return

    print(f"Executing {len(test_cases)} test cases")

    llm = ChatAzureOpenAI(model="gpt-4.1")
    #llm = ChatOllama(model="llama3.1:8b")
    for test_case in test_cases:
        test_id, title, description, steps = (
            test_case['test_id'], test_case['title'], test_case['description'], test_case['steps']
        )

        print(f"Running Test {test_id}: {title}")

        task_execute_single = f"""
        Execute THIS SINGLE TEST CASE and update database.

        Session ID: {session_id}
        Test ID: {test_id}
        Title: {title}
        Description: {description}
        Steps: {steps}
        Target URL: {url}

        Instructions:
        1. Navigate to {url}
        2. Execute steps
        3. Call 'Update Test Case Status' ONCE
        """


        agent = Agent(task=task_execute_single, llm=llm, tools=tools, max_actions_per_step=1,
                      browser=Browser(headless=True, window_size={'width': 1920, 'height': 1080}))
        await agent.run()

        async with pool.acquire() as conn:
            result = await conn.fetchrow(
                "SELECT status, comment FROM test_cases WHERE session_id = $1 AND test_id = $2",
                session_id, test_id
            )

        status = result['status'] if result else 'unknown'
        comment = result['comment'] if result else ''

        print(f"Test {test_id} finished: {status}")

        try:
            allure_reporter.create_test_result(
                session_id=session_id,
                test_case=test_case,
                status=status,
                error_message=comment
            )
        except Exception as e:
            print(f"Allure error: {e}")

    async with pool.acquire() as conn:
        await conn.execute("UPDATE sessions SET status = 'Completed' WHERE session_id = $1", session_id)

    print(f"Session {session_id} complete")
    print("Generating Allure report...")

    if allure_reporter.generate_report():
        print("Allure report generated")
    else:
        print("Failed to generate Allure report")

@app.post("/api/generate-testcases")
async def generate_test_cases(request: GenerateTestCasesRequest, background_tasks: BackgroundTasks):
    try:
        session_id = generate_session_id()
        background_tasks.add_task(create_test_plan, request.url, request.num_test_cases, session_id)
        return {
            "message": "Test case generation started",
            "session_id": session_id,
            "url": request.url,
            "num_test_cases": request.num_test_cases,
            "status": "In Progress"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/GetSession/{session_id}", response_model=SessionSummaryResponse)
async def get_session(session_id: str):
    try:
        summary = await get_session_summary(app.state.pool, session_id)
        if not summary["session"]:
            raise HTTPException(status_code=404, detail="Session not found")
        summary["session"]["status"] = summary["session"].get("status") or "In Progress"
        return summary
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/GetAllSessions", response_model=List[SessionResponse])
async def get_all_sessions():
    try:
        sessions_data = await get_all_sessions_data(app.state.pool)
        result = []
        for session_data in sessions_data:
            test_cases = [
                TestCaseResponse(
                    id=tc['id'],
                    test_id=tc['test_id'],
                    title=tc['title'],
                    description=tc['description'],
                    steps=tc['steps'],
                    status=tc['status'] or 'Pending',
                    comment=tc['comment'] or '',
                    executed_at=tc['executed_at']
                )
                for tc in session_data['test_cases']
            ]

            result.append(SessionResponse(
                session_id=session_data['session_id'],
                url=session_data['url'],
                num_test_cases=session_data['num_test_cases'],
                created_at=session_data['created_at'],
                status=session_data['status'],
                test_cases=test_cases
            ))

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/execute-session/{session_id}")
async def execute_session(session_id: str, background_tasks: BackgroundTasks):
    try:
        pool: asyncpg.Pool = app.state.pool
        async with pool.acquire() as conn:
            session = await conn.fetchrow("SELECT * FROM sessions WHERE session_id = $1", session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        background_tasks.add_task(execute_test_plan, session_id)
        return {"message": "Execution started", "session_id": session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/allure-results")
async def get_allure_results():
    results_dir = "allure-results"
    if os.path.exists(results_dir):
        files = os.listdir(results_dir)
        return {"allure_results": files, "count": len(files)}
    return {"allure_results": [], "count": 0}

@app.get("/api/test-sessions")
async def get_all_sessions_with_allure():
    pool: asyncpg.Pool = app.state.pool
    async with pool.acquire() as conn:
        sessions = await conn.fetch("SELECT * FROM sessions ORDER BY created_at DESC")
        return [dict(session) for session in sessions]

@app.post("/api/generate-allure-report")
async def generate_allure_report():
    try:
        success = allure_reporter.generate_report()
        if success:
            return {
                "message": "Report generated",
                "report_path": "/app/allure-reports/index.html",
                "status": "success"
            }
        else:
            return {"message": "Failed", "status": "error"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.on_event("startup")
async def startup_event():
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    await init_database(app.state.pool)

@app.on_event("shutdown")
async def shutdown_event():
    pool: asyncpg.Pool = app.state.pool
    await pool.close()

@app.get("/")
async def root():
    return {"message": "Test Automation API running"}

@app.get("/health")
async def health():
    try:
        pool: asyncpg.Pool = app.state.pool
        async with pool.acquire() as conn:
            await conn.fetchrow('SELECT 1')
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
