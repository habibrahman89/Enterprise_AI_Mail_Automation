import json
import logging

from models import db, AutomationRule, Task

log = logging.getLogger(__name__)

# Only these Email fields may be referenced by an automation rule's
# condition JSON. This is a defense-in-depth whitelist so a rule can
# never be used to probe or branch on attributes/relationships that
# were never intended to be exposed to rule authors.
ALLOWED_CONDITION_FIELDS = {
    "provider", "sender", "recipients", "cc", "subject", "folder",
    "category", "priority", "risk_level", "action_required",
    "ai_processed", "is_read",
}


def process_rules(email):

    rules = AutomationRule.query.filter_by(
        enabled=True
    ).all()

    for rule in rules:

        try:

            condition = json.loads(
                rule.condition_json or "{}"
            )

            action = json.loads(
                rule.action_json or "{}"
            )

            matched = True

            # ------------------------------------------------
            # Evaluate conditions
            # ------------------------------------------------

            for key, expected in condition.items():

                if key not in ALLOWED_CONDITION_FIELDS:
                    log.warning(
                        "Automation rule %s references disallowed field %r; skipping rule.",
                        rule.id, key,
                    )
                    matched = False
                    break

                actual = getattr(
                    email,
                    key,
                    None
                )

                # List condition
                if isinstance(expected, list):

                    if actual not in expected:
                        matched = False
                        break

                # Wildcard string
                elif (
                    isinstance(expected, str)
                    and expected.startswith("*")
                    and expected.endswith("*")
                ):

                    search_value = (
                        expected.strip("*")
                        .lower()
                    )

                    if search_value not in str(
                        actual or ""
                    ).lower():

                        matched = False
                        break

                # Exact comparison
                else:

                    if actual != expected:

                        matched = False
                        break

            # ------------------------------------------------
            # Rule matched
            # ------------------------------------------------

            if not matched:
                continue

            # ------------------------------------------------
            # Create task
            # ------------------------------------------------

            if action.get("create_task"):

                task = Task(
                    email_id=email.id,

                    title=action.get(
                        "task_title",
                        "Review email"
                    ),

                    description=(
                        email.summary
                        or email.subject
                    ),

                    priority=action.get(
                        "priority",
                        email.priority
                    )
                )

                db.session.add(task)

            # ------------------------------------------------
            # Category
            # ------------------------------------------------

            if action.get("mark_category"):

                email.category = action[
                    "mark_category"
                ]

            # ------------------------------------------------
            # Risk
            # ------------------------------------------------

            if action.get("risk_level"):

                email.risk_level = action[
                    "risk_level"
                ]

        except (json.JSONDecodeError, TypeError, ValueError) as exc:

            log.error(
                "Automation rule %s has invalid condition/action JSON: %s",
                rule.id, exc,
            )

            # Do NOT rollback here.
            # Let the main transaction handle it.

    return email