"""Synthetic fixture shared by all backends; amounts are fictional USD."""
EMPLOYEES = [
    {"name": "Alice", "department": "Engineering", "salary": 120000, "experience": 6},
    {"name": "Bob", "department": "Engineering", "salary": 100000, "experience": 4},
    {"name": "Carol", "department": "Sales", "salary": 90000, "experience": 3},
    {"name": "Dan", "department": "Sales", "salary": 110000, "experience": 5},
    {"name": "Eve", "department": "Research", "salary": 140000, "experience": 8},
    {"name": "Frank", "department": "Research", "salary": 130000, "experience": 7},
]
SALES = [("Engineering", 10000), ("Sales", 25000), ("Sales", 15000), ("Research", 5000)]
# Directed manager -> report edges; shortest paths use the undirected projection.
REPORTS = [("Alice", "Bob"), ("Alice", "Carol"), ("Carol", "Dan"), ("Eve", "Alice"), ("Eve", "Frank")]

