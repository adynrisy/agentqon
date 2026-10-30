# Multi-Agent Harness

A multi-agent harness is software that puts more than one AI agent to work on a task, and keeps their work organised. One agent on its own can answer a question or draft a piece of work, but it has no one to check it. A harness adds structure around the agents: it plans the work, assigns it, checks the results, and hands back an answer that can be traced to its evidence.

AgentQon is a multi-agent harness of a particular kind. It does not hand each agent a separate piece of a job and then stitch the pieces together. Instead, it runs a panel of agents on the same mission, lets a critic test their drafts, scores what survives, and labels the result with its evidence. This page explains that panel model, how it differs from the more familiar coding-fleet pattern, and what happens when a mission ends.

## The coding-fleet pattern

In the generic coding-fleet pattern, a large job is broken into smaller coding subtasks, and each agent is given one subtask. One agent might write one part of a program, another might write a different part, and a third might write tests. The work is then combined into a single result.

That pattern makes sense when a job can be divided cleanly. Its weakness is plain: each agent works on only its own slice, so no agent sees the whole job in the same way. If two slices do not fit together, the problem may only show up when the pieces are joined.

## The panel model: the same mission, more than once

AgentQon takes a different route. A mission is given to a panel of specialist agents, and each agent works on the same mission. The agents do not divide the mission between them. They produce rival drafts of a full answer, so the drafts can be compared with each other rather than simply added together.

A planner first breaks the mission into a task graph, so the work has a clear shape before drafting begins. Specialist agents are then picked from the roster for this particular task, based on measured suitability for the work in hand. Each selected agent drafts an answer to the mission.

A critic then attacks every draft. The critic looks for weak reasoning, unsupported claims, gaps, and mistakes. Weak work is revised or rejected. It does not pass through untouched because it was produced confidently. An evaluator then scores what survives, and the surviving draft with the strongest score becomes the answer that is returned.

This is the core difference between the two patterns. In the coding-fleet pattern, agents cooperate by dividing the work. In the panel model, agents compete on the same work, and a critic and an evaluator stand between their drafts and the final answer. The question is not only whether work was produced, but whether it held up when it was challenged.

## Labelled outcomes, not silent stops

A mission in AgentQon runs to a labelled outcome. That means the result is marked as finished, or it is marked as failed with its evidence. It does not simply stop without explanation, leaving the user to guess whether the work is done, incomplete, or abandoned.

A failed mission still carries information. It shows what was tried, where the work broke down, and what evidence was gathered along the way. A finished mission shows the answer together with the evidence behind it. In both cases, the user can see what happened, rather than receiving a partial answer presented as if it were complete.

If no model is connected, work refuses to run and explains how to connect one, rather than presenting filler text as agent work.

## Evidence labels and the Proof Pack

Every result carries evidence labels in a Proof Pack. The Proof Pack contains the answer, the evidence behind each claim in that answer, what the critic found, and what the work cost. The labels make it possible to trace a claim back to its source, instead of taking the answer on trust.

A performance ledger remembers which agents actually deliver, so later panels are picked with that record in mind.

## Models and where the work runs

AgentQon is free, with no paid tier and no account or signup required. The model is connected by the user's own key, a practice known as BYOK, or the user can run a free local model on their own computer. When a provider key is used, the provider may bill for that use, so the cost of the model is separate from AgentQon itself, which charges nothing.

Everything runs on the user's machine. Missions, keys, and data stay there, and nothing goes anywhere except to the model the user has connected, under the user's approval for tool steps such as reading and writing files.

## The roster and the skills behind the panel

The panel draws on a large roster and skill library. AgentQon includes 6,799 agents, each with an inspectable record, and 4,840 skills built in, retrieved automatically for each task. There is also an optional 213-skill Extended Library, offered as a separate optional pack.

Whatever the size of a mission, the same steps apply: plan, draft on the same mission, criticise, score, label the outcome, and attach the evidence.

## Getting started

To see the panel model in practice, start with the main AgentQon page, which describes the harness in full and links to its guides:

[AgentQon repository](https://github.com/adynrisy/agentqon)

AgentQon is also listed on npm, where the install command is shown:

[agentqon on npm](https://www.npmjs.com/package/agentqon)

Install it with:

```
npm install -g agentqon
```

Then run `agentqon` and give it a mission. The panel will plan the work, draft competing answers, criticise and score them, and return a labelled result with its Proof Pack — finished, with its evidence, or failed, with its evidence.
