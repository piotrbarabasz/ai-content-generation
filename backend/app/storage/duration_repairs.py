"""Append-only D077 evidence, using existing project-owned artifact publication."""

from app.domain.base import new_id
from app.domain.duration_repair import DurationRepairPolicy
from .narrative_reviews import ProjectNarrativeRecords


class ProjectDurationRepairs:
    def __init__(self, repository, store):
        self.records = ProjectNarrativeRecords(repository, store)

    def save(self, kind, value):
        return self.records.save("duration_" + kind, {"version": 1, **value})

    def history(self, kind, *, plan_id=None, campaign_id=None):
        values = [self.records.read(m) for m in self.records.history("duration_" + kind)]
        return tuple(v for v in values if (plan_id is None or v.get("plan_id") == plan_id)
                     and (campaign_id is None or v.get("campaign_id") == campaign_id))

    def policy(self, plan_id):
        values = self.history("policy", plan_id=plan_id)
        if not values:
            return DurationRepairPolicy(), None
        value = values[-1]
        return DurationRepairPolicy.from_payload(value["policy"]), value["campaign_id"]

    def configure(self, plan_id, policy):
        _, campaign = self.policy(plan_id)
        campaign = campaign or new_id("duration_campaign")
        self.save("policy", {"plan_id": plan_id, "campaign_id": campaign, "policy": policy.to_payload()})
        return campaign

    def budget_for_revision(self, plan_id, revision_id):
        for value in reversed(self.history("commit", plan_id=plan_id)):
            for budget in value["budgets"]:
                if budget["revision_id"] == revision_id:
                    return budget["target_words"]
        return None
