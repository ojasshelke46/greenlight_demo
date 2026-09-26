"""The policy feature: the target repo's .greenlight.yml decides who may approve a merge, how many
approvals it needs, when merges are frozen, and whether the agent may make major upgrades."""

from app.features.policy.checks import majors_message_part, policy_check
from app.hooks import register_approval_check, register_run_message_part

register_approval_check(policy_check)
register_run_message_part(majors_message_part)
