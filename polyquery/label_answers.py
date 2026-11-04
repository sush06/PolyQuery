"""Validity scoring for draft labels whose correct answers are non-unique.

References must come from an independently prepared eligible-row set or graph,
never from the router being evaluated. These checks do not score extraction.
"""
from collections import deque
from .evaluation import equivalent


def score_label_answer(case, response, reference):
    """Score an {status: 'ok', value: ...} response against label policy.

    Reference is all eligible projected rows for unordered_valid_subset, or an
    adjacency mapping for bounded_shortest_path. Unknown policies fail loudly.
    """
    policy = case['context']['answer_policy']
    scorer = policy['scorer']
    if scorer not in ('unordered_valid_subset', 'bounded_shortest_path'):
        raise ValueError('Unsupported answer validity policy: ' + scorer)
    if response.get('status') != 'ok':
        return False
    value = response.get('value')
    if scorer == 'unordered_valid_subset':
        fields = set(case['context']['projection']['fields'])
        identity = policy['identity']
        if any(set(row) != fields for row in reference):
            raise ValueError('Reference must contain exactly the projected fields')
        if len({row[identity] for row in reference}) != len(reference):
            raise ValueError('Reference identity is not unique')
        if not isinstance(value, list) or len(value) != min(policy['limit'], len(reference)):
            return False
        seen = set()
        for row in value:
            if not isinstance(row, dict) or set(row) != fields:
                return False
            key = row[identity]
            if not isinstance(key, str) or key in seen:
                return False
            seen.add(key)
            if not any(equivalent(row, expected) for expected in reference):
                return False
        return True
    source, target = policy['source'], policy['target']
    # The policy explicitly uses undirected friendship edges.
    graph = {node: set(neighbors) for node, neighbors in reference.items()}
    for node, neighbors in list(graph.items()):
        for neighbor in neighbors:
            graph.setdefault(neighbor, set()).add(node)
    queue = deque([(source, 0)])
    visited = {source}
    distance = None
    while queue:
        node, hops = queue.popleft()
        if node == target:
            distance = hops
            break
        if hops == policy['max_hops']:
            continue
        for neighbor in graph.get(node, ()):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, hops + 1))
    if distance is None:
        return 'value' in response and value is None
    return (isinstance(value, list) and len(value) == distance + 1
            and value[0] == source and value[-1] == target
            and all(isinstance(node, str) for node in value)
            and all(b in graph.get(a, ()) for a, b in zip(value, value[1:])))
