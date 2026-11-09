# Working on Dorker

## History and purpose

### What was the problem I was trying to solve by this thing?

It was current job marked. I found an issues, that applying to jobs for me get's extremely difficult, and so
finding them. I felt overqualified by knowledge and skills and underqualified by a single number
"years of experience" in resume. Standard junior roles were to trivial for me, but at the senior I was
genuinely ignored either by ATS or by recruiter, because of specific tool, yoe, etc. So just to automate
this routine, where I could spend hours writing a single cover letter, I decided to build my own system.

### Why not proprietary tools?

I know how ML and LLMs work, so do how backend engineering. But before start, I researched market a bit,
and understood that no tool provides spectre of features I would like to see in my application. And
moreover, I completely do not understand how they match me under the hoot. I have specific list of
requirements I would like to follow, so that proprietary platforms like [sprout](https://usesprout.com) or standard ai
slop auto
appliers didn't work for me. I planned to do this as a startup, however after some product research I
understood that the LTV of the user is to low, because the better I match -> the faster my user finds a
job -> the less time it spends on platform. So since I can't earn money on it, I decided to make it
opensource.

### Project base, implementation details and some of short ADR overview

I started from base repository of ATS scrapers [kalil0321/ats-scrapers](https://github.com/kalil0321/ats-scrapers) with
already prepared tenants.
The hardest part was to explore and understand this repository, but with enough time and effort I configured an
automated pipeline, with CLI UI, on top of Postgres. Initially I started from SQLite, but when
concurrency question arise, I decided to not waste time and just get to tool where I'm already feel
comfortable. The hardest thing in scraper itself was the question: how I filter the millions of records
where each one is a unique job from one of 40+ platforms, and do it fast and reducing False negatives?
So I integrated vector search with pg_vector, set up enough indexes, and configured it finally to output
me enough leads from a millions rows database. At first it was around 2500-3000 jobs. The next question
was the hardest to solve: how do I now match the job to me by my specific preferences? In this case pure
end-to-end matching fails, because BERT models or just standard cosine similarity evaluate key-words
only, not their general meaning in the way I wanted. To be specific, here is the example: the job
requires python, java, AWS, only in title it specifies that the role is Senior, and so comparing to my
profile, it seems both relevant and irrelevant at the same time, and here is why. I have experience in
Python and not a Senior (yet), and I also do not have experience in Java and AWS. However, if it is a
Nice-to-haves block, I already familiar with C# and .NET, which are really close to Java, so I'll be
comfortable to use them, I also have hands-on experience with GCP, so I won't have troubles learning and
switching to AWS, and also the role specifies that the Senior in the title is not so important, because
they value how you think, and not yoe, so I'm genuinely the match. That's why I decided to build
something personal. The criteria was "fast and cheap". OpenAI or GoogleAI API wasn't suitable for me at
that point, because it would be the waste of expenses, since I'll spend the same amount of money to the
role that somewhere downwards specify that US only candidates required, and for the role that I'm 100%
fit. But the local SLMs on another hand tend to hallucinate, has less context and would genuinely give
worse results. When I was testing the same prompt on the SLM which was designed for OpenAI ChatGPT 5,
the results were terrible, I'm not gonna lie. Some of the examples would be that the model confused my
profile description with a job and gave a tier SUITABLE to the pure Marketing role. So that I needed to
ensure, that model not just give the right result, but thinks in the right direction. And the thing that
saved me at that moment was Microsoft guidance library discovery. This thing allowed me not just prompt
a model, but decide in which direction it things. But I needed to evaluate it somehow, and moreover optimize it's
inference quality and time.
So that I created a Jupiter notebook and started working. I've trained a classifier using nomic
embedder, to identify 6 types of description blocks (like Responsibilities, Requirements, About company, Legal, etc).
This allowed me to strip description
and pass to a model better quality and higher density info,
so that inference ran faster. I also build a golden set out of ~100 jobs, and to do that, I used
farthest point method, where I collected all technical jobs,
that vary the most out of a pool, like Bio science, Data analyst, Go-to-market, etc., and marked it by
hand. Why not with AI? Because it allowed me to understand better,
which info to feed to the model, which strip, what exactly specify, and what request back. For closer
matches, I wrote ideal job description, embedded it and compared against other jobs.
That didn't gave me the perfect much, however balanced my dataset. And all that labeling and guidance
increased recall and precision drastically.

However, just at the night the first benchmarks passed and pipeline finally worked,
I discovered the [Jev model by TypeSafe AI](https://docs.typesafe.ai/introduction), that came out 6 days ago, and was in a closed access (at that
moment). At the next morning, I immediately claimed access and rewrited my pipeline.
In the result: 100 jobs that were processed by guidance in 5 hours, passed in less than a minute with a
Jev. That was an insane result.
So I migrated pipeline to Jev, and the hardest part left: how do I generate cover letter, which is not a
generic AI slop, and not just copies my writing style, but demonstrates the way I think.
I decided to do that, because I have ton of CoverLetter written manually, when I was applying to jobs,
and since they are not generated by AI, I would like to use them as a template.
And then, my "gold mine" was my Canonical written interview, which I was writing manually for over a
week, and which allowed me to pass to the next round of the process.
Its text not just describes what I do. It shows the way I think, behave, solve the problems, and
ideology or principles I adhere to. That's what I would like to show every employer,
when contacting it first. Over a 20 A4 Roboto 14 pages were pure text, to compile a perfect cover
letter. But the question raised again: how do I feed a smarter model as much dense context as possible,
without wasting money on tokens and encountering lost in the middle problem? So that Jev again came up:
I used it to evaluate each block of my 35k+ tokens profile, and classify whether
it would be the right thing to mention in application. After this my experience is carefully selected
before generation and total context stripped from 35k+ to around 5-7k.
And so my cover letters not just generic AI slop, they are the thing I'm not ashamed of, and would
basically read by myself.

### Why is this name (just want it to be here XD)?
And everything's from this verbose overview name is Dorker. Why so? Because it's funny pun, where dork
is something describing smarty-pants, bookworm, nerd and I personally used
Google DORKS to discover job descriptions, when I ran out of public job boards.

## My engineering beliefs and effort I want to put into this project
Even in the age of AI I want to still remain an engineer who is responsible for his own code and its quality. 
I'm neither hater nor a wide adopter of agentic engineering. So that I hold next beliefs in order to AI use:
1. Code you write manually is not just symbols. That's a mental model you build in your head. Without it you're incapable of making worth architectural choices. With every function definition written and argument passed you understand, why this is the way. With every friction encountered, you modify the codebase to better suit your needs and increase speed and quality.
2. While AI can write boiler-plate code, only engineer truly defines what this "boiler-plate" means for him. For Senior boiler-plate might be "spin-up kubernetes cluster", but for Junior "add SQLite support alongside with PostgreSQL" might be a real task it should build manually.
3. If the project requires me to reject idea of writing code manually, then it basically steals from me my personal development and growth. I could automate anything with AI, but as soon as automation starts transitioning to natural language, it becomes chatting, not an engineering. I can't make decisions, see their results and adapt my approach.

That why I would like to comment about approaches I would like to use throughout the development of this project.

## Engineering practices I follow within this project
Here are top practices ordered from the most to the least preferable. They are basically short overview of [skills definition]()
1. $investigate - this is the approach, where we plan something only, you, as an agent can access specified resources and provide options to implement
2. $build - this is the approach, where you build, but alongside with me, it might include planning only, but the difference from investigate is that you're allowed to introduce changes within codebase. However you're still not an autonomous agent who's leading task implementation end-to-end. That's basically the flow, where we're developing core feature, where my own understanding required for further project evolution.
3. $handoff - this is the least preferred and used option where I give you full control over implementation. This might be useful for minor refactoring, integration, etc., where I have enough knowledge so I can specify requirements as unambiguos as possible and result is already fully understood by me. 

## Align before implementing

- Read the relevant code and task references first. Describe existing behavior
  separately from intended behavior; point out contradictions and uncertainty.
- For ambiguous features or architectural changes, present a short implementation
  brief before editing: intended behavior, consequential choices, affected
  components, scope exclusions, and concrete acceptance examples. Recommend an
  approach and explain the tradeoff. Wait for agreement on unresolved choices.
- Examples of consequential choices: state ownership and restart behavior, schema
  changes, public interfaces, new runtime dependencies, model/provider changes,
  scoring semantics, and what candidate data is sent to external services.
- An explicit task that already settles these choices authorizes execution.
  Routine fixes and documentation edits can proceed after a concise scope update.
  Resolve local implementation details independently; avoid repeated approval
  requests for decisions already made.
- Implement a small, reviewable slice first when the behavior is still being
  explored. Return to the maintainer if new evidence requires changing the agreed
  behavior or architecture. Continue independent investigation while clarifying.
- Keep unrelated refactors and improvements out of the task. Report them
  separately. Preserve existing user changes and inspect Git status before editing.

## Keep decisions available

- Use the current task and explicitly accepted decisions as the behavioral
  reference. Existing code is evidence of implementation, not proof of intent.
- Read the Notion pages, diagrams, or repository documents named in the task.
  If access fails, report the missing context and its impact; do not invent it.
- Record consequential accepted decisions in the task's designated document:
  decision, reason, affected components, and status (accepted or superseded).
  Keep proposals and unresolved questions clearly labeled.
- Use Notion for collaborative documentation and Excalidraw for editable diagrams
  when requested. Do not assume external documents stay synchronized with code.
  Identify the reference used and update the authorized documentation destination.
- Keep this file focused on durable working agreements. Feature-specific details
  belong in their task brief or design document. Propose changes to project-wide
  rules when repeated corrections reveal a useful convention.

## Repository map

| Location                      | Responsibility                                                                      |
|-------------------------------|-------------------------------------------------------------------------------------|
| `orchestration/scraper.py`    | Configures and runs scraping, persistence, CLI progress, and telemetry              |
| `shared/scripts/normalizer.py` | Normalizes stored job descriptions in PostgreSQL                                    |
| `orchestration/analyst.py`    | Wires analytics, model providers, discussion, repositories, and Telegram            |
| `src/scraping/`               | ATS adapters, fetching, scraping pipeline, configuration, and scraping repositories |
| `src/analytics/`              | Matching, classification, profile materials, generation, and discussion             |
| `src/client/telegram.py`      | Telegram commands, callbacks, and user interaction                                  |
| `src/database/`               | Analytics repository protocols and PostgreSQL implementations                       |
| `src/shared/models/`          | Shared data models and pipeline version information                                 |
| `migrations/`                 | PostgreSQL and legacy SQLite migration scripts                                      |
| `configs/`, `artifacts/`      | Scraper configuration, prompts, datasets, and model artifacts                       |
| `tests/`                      | Currently unavailable, will be configured and maintained soon                       |

Follow the surrounding async Python, typed models, and protocol/repository patterns.
Inspect imports before copying examples: the checkout currently uses both `src.*`
and package-level imports. Avoid broad import or packaging rewrites as side effects.

Scrapers derive from `kalil0321/ats-scrapers`. Read
`.github/workflows/sync-scrapers.yml` before modifying synchronized adapters or tests,
and consider how local changes survive upstream synchronization. Preserve attribution.

## Environment and validation

- Run commands from the repository root in the project's Python environment.
  `environment.yml` describes the Conda environment and currently specifies Python
  3.13; some source syntax requires Python 3.12 or later. Check the active interpreter
  and dependencies before diagnosing environment failures as application defects.
- `pyproject.toml` configures Ruff (100-character lines, Python 3.12 target) and
  strict mypy. Use the existing configuration rather than weakening checks.
- Available validation commands, subject to installed project dependencies:
    - Mocked discussion tests: `python -m unittest discover -s tests`
    - Lint changed Python files: `python -m ruff check <changed-python-files>`
    - Type checks: `python -m mypy --no-incremental` (configured for `src` and `orchestration`)
- These commands are guidance, not a claim that the current checkout passes them.
  Distinguish existing failures, environment limitations, and regressions from the task.
- For behavior changes, verify agreed acceptance examples and meaningful failure
  cases. Mock network/model calls where possible. Tests should exercise intended
  behavior rather than merely reproduce the implementation's assumptions.
- For documentation-only changes, verify paths, commands, and consistency; no
  application test run is required. After relevant checks pass, repeat them only
  when changes or unresolved concerns justify it.

## Runtime and documentation accuracy

- The Compose file is `deployment/docker-compose.yml`; it defines PostgreSQL and
  backups. Python entry points run separately: `python -m orchestration.scraper` and
  `python -m orchestration.analyst`.
- Inspect entry points before running them: they can access databases, job sites,
  Telegram, AI providers, local model files, and telemetry. Starting the application
  or applying a migration is not a routine offline test.
- In architecture diagrams, distinguish Python modules, running processes, external
  services, and storage. Label communication type and data exchanged. Mark optional,
  disabled, and proposed paths; inspect the current wiring rather than assuming all
  implemented methods run (background classification is currently commented out
  in the analyst entry point).
- Use synthetic candidate examples in public documentation and tests. Keep private
  profile materials, credentials, and personal discussion data out of public artifacts.

## Completion and handoff

- Review the diff against the agreed scope and acceptance examples before reporting
  completion. Explain changed behavior, consequential decisions, checks actually run,
  and any remaining limitations. Never claim a check or integration was verified
  without evidence.
- Leave changes reviewable. Commit, publish, merge, deploy, or write to external
  documentation when that action is authorized by the task.
- Add skills or recurring automations after the workflow proves useful in practice;
  give each a bounded purpose and a clear completion condition.
