"""Keep allocations, losses and task charges separate; clamp only the displayed balance."""

UNSET = object()


def change_budget(project, *, lost_delta=0, spent_delta=0, remaining=UNSET):
    lost = project.time_lost_minutes + lost_delta
    spent = project.time_spent_minutes + spent_delta
    budget = project.time_budget_minutes
    if remaining is not UNSET:
        budget = None if remaining is None else remaining + lost + spent
    assert lost >= 0 and spent >= 0
    return {
        "time_budget_minutes": budget,
        "time_lost_minutes": lost,
        "time_spent_minutes": spent,
        "state": {
            **project.state,
            "available_minutes": None if budget is None else max(0, budget - lost - spent),
        },
    }
