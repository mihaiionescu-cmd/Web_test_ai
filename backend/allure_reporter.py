import json
import os
from datetime import datetime
import uuid
import subprocess


class AllureReporter:
    def __init__(self, results_dir="/app/allure-results", reports_dir="/app/allure-reports"):
        self.results_dir = results_dir
        self.reports_dir = reports_dir
        os.makedirs(results_dir, exist_ok=True)
        os.makedirs(reports_dir, exist_ok=True)

    def create_test_result(self, session_id, test_case, status, error_message=None):
        steps = test_case.get("steps", [])
        steps_str = "\n".join(steps) if isinstance(steps, list) else str(steps)

        status_map = {
            "Pass": "passed",
            "Fail": "failed",
            "Failed": "failed",
            "Passed": "passed",
            "Blocked": "skipped",
            "Skipped": "skipped",
            "Pending": "unknown"
        }
        allure_status = status_map.get(status, status.lower())

        ts = int(datetime.now().timestamp() * 1000)
        test_result = {
            "name": f"{test_case['test_id']}: {test_case['title']}",
            "status": allure_status,
            "start": ts,
            "stop": ts,
            "uuid": str(uuid.uuid4()),
            "historyId": f"{session_id}_{test_case['test_id']}",
            "fullName": f"{session_id}.test_case_{test_case['test_id']}",
            "labels": [
                {"name": "suite", "value": f"Session_{session_id}"},
                {"name": "feature", "value": "LLM_Test_Generation"},
                {"name": "framework", "value": "browser-use"}
            ],
            "steps": [
                {
                    "name": step,
                    "status": "passed",
                    "start": ts,
                    "stop": ts
                } for step in steps_str.split("\n") if step.strip()
            ]
        }

        if error_message:
            test_result["statusDetails"] = {
                "message": error_message,
                "trace": error_message
            }

        filename = os.path.join(self.results_dir, f"{test_result['uuid']}-result.json")
        with open(filename, 'w') as f:
            json.dump(test_result, f)

        return test_result['uuid']

    def generate_report(self):
        import shutil

        if not shutil.which("allure"):
            print("Allure CLI not found.")
            return False

        try:
            result = subprocess.run(
                ["allure", "generate", self.results_dir, "-o", self.reports_dir, "--clean"],
                check=True, capture_output=True, text=True
            )
            print("Report generated.")
            return True
        except subprocess.CalledProcessError:
            print("Report generation failed.")
            return False
