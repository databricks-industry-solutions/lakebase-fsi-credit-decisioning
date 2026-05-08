"""run_pipeline.py — Generate a fresh Lakebase credential and trigger the pipeline job.

Run this script locally (from the project root) before each pipeline execution:

    python3 scripts/run_pipeline.py

It generates a short-lived Lakebase credential via the SDK, injects it into the
job's base_parameters, and triggers a run. The credential is valid for ~15 minutes —
enough for the full pipeline to complete.

Requirements:
- Databricks CLI configured with a profile (set DATABRICKS_PROFILE env var or edit below)
- Secret scope 'lakebase-demo' with key 'endpoint_path' set in the target workspace
- Job 'lakebase-credit-pipeline' already created (run RUNME.py once to create it)
"""

import os
import sys

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.jobs import Task, NotebookTask, TaskDependency, JobSettings

PROFILE  = os.environ.get("DATABRICKS_PROFILE", "")
JOB_NAME = "lakebase-credit-pipeline"

w = WorkspaceClient(profile=PROFILE) if PROFILE else WorkspaceClient()

endpoint_path = w.secrets.get_secret("lakebase-demo", "endpoint_path").value
if not endpoint_path:
    sys.exit("Secret 'lakebase-demo/endpoint_path' is empty. Set it first.")

cred = w.postgres.generate_database_credential(endpoint=endpoint_path)
lb_token = cred.token
print(f"Credential generated (len={len(lb_token)})")

jobs = [j for j in w.jobs.list() if j.settings.name == JOB_NAME]
if not jobs:
    sys.exit(f"Job '{JOB_NAME}' not found. Run RUNME.py first to create it.")

job = jobs[0]
job_id = job.job_id

current = w.jobs.get(job_id=job_id)
current_tasks = current.settings.tasks

updated_tasks = []
for t in current_tasks:
    nb = t.notebook_task
    updated_params = {**(nb.base_parameters or {}), "lakebase_pass": lb_token}
    updated_tasks.append(Task(
        task_key=t.task_key,
        notebook_task=NotebookTask(
            notebook_path=nb.notebook_path,
            base_parameters=updated_params,
        ),
        depends_on=t.depends_on,
    ))

w.jobs.reset(
    job_id=job_id,
    new_settings=JobSettings(name=JOB_NAME, tasks=updated_tasks),
)
print(f"Job {job_id} updated with fresh credential")

run = w.jobs.run_now(job_id=job_id)
print(f"Pipeline triggered: run_id={run.run_id}")
ws_host = w.config.host.rstrip("/")
print(f"Monitor: {ws_host}/#job/{job_id}/run/{run.run_id}")
