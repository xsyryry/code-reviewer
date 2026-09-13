# Revision Task

You are given a bug report, a previous patch attempt, and a detailed code review.
Use the review feedback to guide your revision.

## Bug Report
Read `/testbed/revision_data/problem_statement.txt`

## Previous Attempt
Read `/testbed/revision_data/original_patch.diff`

## Code Review Feedback
Read `/testbed/revision_data/review_feedback.txt`

Apply the previous patch as a starting point:
`cd /testbed && git apply /testbed/revision_data/original_patch.diff`
Then address each defect identified in the review, focusing on HIGH severity issues.
