"""Search the public dataset for ML engineering jobs in Paris."""

from jobhive import search

df = search(query="machine learning", location="Paris", limit=10)
print(df[["title", "company", "location", "salary_summary"]])
