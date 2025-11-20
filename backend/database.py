import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
import asyncpg
from dotenv import load_dotenv



load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")


async def init_database(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        # Sessions table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                num_test_cases INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'In Progress'
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS test_cases (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                test_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                steps TEXT NOT NULL,
                status TEXT DEFAULT 'Pending',
                comment TEXT DEFAULT '',
                executed_at TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            )
        """)

        await conn.execute("UPDATE test_cases SET status = 'Pending' WHERE status IS NULL")
        await conn.execute("UPDATE sessions SET status = 'In Progress' WHERE status IS NULL")

        print("PostgreSQL Up")


def generate_session_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


async def get_session_summary(pool: asyncpg.Pool, session_id: str) -> dict:
    async with pool.acquire() as conn:
        session = await conn.fetchrow("SELECT * FROM sessions WHERE session_id = $1", session_id)

        stats_rows = await conn.fetch("""
            SELECT COALESCE(status, 'Pending') AS status, COUNT(*) as count 
            FROM test_cases 
            WHERE session_id = $1 
            GROUP BY status
        """, session_id)
        stats = {row['status']: row['count'] for row in stats_rows}

        # Get all test cases
        test_cases = await conn.fetch("""
            SELECT test_id, title, COALESCE(status, 'Pending') AS status, COALESCE(comment, '') AS comment, executed_at
            FROM test_cases 
            WHERE session_id = $1
            ORDER BY test_id
        """, session_id)

    return {
        "session": dict(session) if session else None,
        "stats": stats,
        "test_cases": [dict(tc) for tc in test_cases]
    }


async def get_all_sessions_data(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        sessions = await conn.fetch("SELECT * FROM sessions ORDER BY created_at DESC")

        result = []
        for session in sessions:
            # Get test cases for this session
            test_cases_raw = await conn.fetch(
                "SELECT * FROM test_cases WHERE session_id = $1 ORDER BY test_id",
                session['session_id']
            )

            session_data = dict(session)
            session_data['status'] = session_data.get('status') or 'In Progress'

            test_cases = []
            for tc in test_cases_raw:
                test_cases.append({
                    'id': tc['id'],
                    'test_id': tc['test_id'],
                    'title': tc['title'],
                    'description': tc['description'],
                    'steps': tc['steps'],
                    'status': tc['status'] or 'Pending',
                    'comment': tc['comment'] or '',
                    'executed_at': tc['executed_at']
                })

            session_data['test_cases'] = test_cases
            result.append(session_data)

    return result