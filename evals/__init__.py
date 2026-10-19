"""Evaluation harness. Deliberately outside `src/kingfisher/`: a synthetic dataset, a task
prompt and an assertion framework are test material, not product.

    dataset    the generated CSV and its computed ground truth
    task       the task text handed to the agent
    checks     structural assertions on result.json
    seed       putting the fixtures into a workspace
    artifacts  reading a finished run's output back
"""
