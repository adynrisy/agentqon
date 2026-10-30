# AI Agent Harness and Agentic Harness

Agent = Model + Harness.

That short formula is the shared definition behind both names on this page: "AI agent harness" and "agentic harness" describe the same thing. There is no need for two separate explanations, because both rest on the same idea. A model on its own can produce text. A harness is what turns that model into an agent that can do work, check its work, and hand back a result you can inspect.

This page explains each part of the formula in plain words, and then shows how AgentQon puts the formula into practice.

## The model

The model is the part that reads and writes language. You give it a request, and it produces an answer, a plan, or a draft. On its own, though, a model has limits that matter for real work. It does not, by itself, remember what happened in an earlier session. It cannot, by itself, open a file, run a command, or look something up. It also has no built-in way to tell you how sure it is, or to show you the evidence behind a claim. It produces a response and stops.

That is why the model is only one half of the formula. The other half is the harness around it.

## The harness

A harness is the system built around the model. It supplies five things: tools, memory, workspace, guardrails, and a verification loop. Each one answers a simple question about how work gets done.

Tools answer the question: what can the agent actually do? A tool is a defined action the agent can take, such as reading a file, writing a file, running a command, or checking a page on the web. Without tools, an agent can only describe work. With tools, it can carry work out, step by step, and show each step as it goes.

Memory answers the question: what does the agent remember? Memory keeps a record of earlier work, earlier decisions, and what worked and what did not. That record lets later work build on earlier work, and lets a user look back and see how a result was reached.

Workspace answers the question: where does the work happen? A workspace is the place where files are read and written, and where the agent's working material lives. A clear workspace matters because it keeps the agent's work in one place, separate from unrelated material, and makes it possible to review what changed.

Guardrails answer the question: what should the agent not do on its own? Guardrails are the limits and approval steps placed around the agent's actions. Some actions need the user's approval before they run. Some actions are refused outright.

The verification loop answers the question: how do you know the result is sound? A verification loop checks the work after it is drafted and before it is handed over. Drafts are reviewed, weak points are challenged, and claims are matched against evidence. Work that does not survive checking is revised or rejected, rather than passed on as if it were finished.

Put together, the harness is what makes an agent different from a single answer in a chat window. The model supplies the language and reasoning. The harness supplies the tools to act, the memory to learn from earlier work, the workspace to work in, the guardrails to stay within bounds, and the verification loop to check the result before delivery.

## How AgentQon instantiates the harness

AgentQon is a free AI agent harness that follows this same formula. You give it a mission — a piece of work that matters, rather than a single small question — and a panel of agents works through it in stages.

A planner breaks the mission into tasks first, so the work has a structure before any drafting begins. Then a panel of up to 5 specialist agents drafts competing answers for the task at hand. The specialists are picked from a roster of 6,799 agents, ranked by suitability for that particular task. A critic then attacks every draft, and weak work is revised or rejected. An evaluator scores what survives, and the best-scoring answer becomes the result you receive.

Every result ships with a Proof Pack: the winning answer, the evidence behind every claim, what the critic found, and what the work cost. The Proof Pack is the verification loop made visible. Instead of taking a result on trust, you can read the evidence and the checking that went with it.

The skill library works in the same practical way. AgentQon carries 4,840 skills built in, pulled in automatically for each task, so the panel has relevant know-how to draw on without the user having to select it by hand. There is also an optional 213-skill Extended Library, offered as a separate optional pack.

For the model itself, AgentQon follows a bring-your-own-key approach, often shortened to BYOK: you connect a model using your own key from a provider you already have. Note that the provider may bill for use under that key, depending on the provider's own terms. Alternatively, you can run a free local model on your own computer. Either way, everything runs on the user's machine, and there is no AgentQon account and no signup step. If no model is connected, work refuses to run and tells you how to connect one, rather than dressing up template text as agent work.

The repository linked below is documentation only.

## Try it

The main AgentQon overview, which this page supports, is the AgentQon repository:

https://github.com/adynrisy/agentqon

To install AgentQon, run:

```
npm install -g agentqon
```

The package page is here:

https://www.npmjs.com/package/agentqon

In short: the model writes and reasons, and the harness gives it tools, memory, a workspace, guardrails, and a verification loop. That combination — Agent = Model + Harness — is what the terms "AI agent harness" and "agentic harness" both mean, and it is the structure AgentQon is built around.
