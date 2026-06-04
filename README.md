# Job analyst
> App used to simplify searching and application process

## Description
This app is an AI driven job analyst. The idea is simple: the market nowadays is ruthless and this is the way to overcome that. Everyone from Junior to Senior feels this pressure searching enourmous amount of sites and job openings just to apply and receive letter "unfortunately,..". This app serves as a pipeline which gathers opened positions, selects relevant ones to your profile (highly configurable due with Master prompt), sends you an application forms, and additionaly tracks application process.
Everything in one place: search, analysis, application tracking. And everything powered with AI (ahaha, not an AI slop btw, that's the problem I've faced, and I'm not vibecoding this).

## Supported job-boards
> Since I'm a Ukrainian there are listed Ukrainian job boards, but I also added worldwide ones, which I use by myself

- [Djinni](https://djinni.co/my/dashboard/) | Ukrainian
- [DOU](https://jobs.dou.ua/) | Ukrainian
- [YCombinator](https://www.workatastartup.com/companies) | Worldwide
- [Golang Cafe](https://golang.cafe/) | Worldwide
- [Golang Bridge](https://forum.golangbridge.org/c/jobs/8) | Worldwide
- [Golang Projects](https://www.golangprojects.com/) | Worldwide
- [GreenHouse](https://job-boards.greenhouse.io/) | Worldwide
- [AshByHq](https://jobs.ashbyhq.com/) | Worldwide
- [Lever](https://jobs.lever.co/) | Worldwide
- [Workable](https://apply.workable.com/) | Worldwide
- [Welcome to the jungle (ex. Otta)](https://app.welcometothejungle.com/) | Worldwide | **Note**: Under review now, due to complex anti-fraud system
- [WellFound (ex. AngelList) ](https://wellfound.com/jobs) | Worldwide | **Note**: Under review now, due to complex anti-fraud system

## Analytics side
The idea is to create a Master promt, that will be attached to each request (and cached for price optimization). I, from my experience, have a 20(+)-pages Written interview, I was preparing for Cannonical application, which describes my whole experience, a bunch of differently formulated CVs and CoverLetters, and I also maybe will compress and attach some of my pet-projects. Also some magic with promt describing matching constraints and the selection criteria, with all dealbreakers, red-flags and other staff.

After sending that to LLM (I will use Gemini at the start) I expect to configure it to send JSON back, with analytics about pros and cons, grade from 0 to 1 whether I should apply, and ofcourse link to the aplication page. If the application score is higher from 0.5, it will be displayed, if not - just marked as processed and stored in db to not process it again.

The application info will be displayed in Telegram bot, with some funcitonality like traking, blocking specific companies, etc. Also I'll probably add a application CoverLetter or FollowUp letter generation, but based on my context, so it does not look like AI generated text, and it is under your consideration - whether to use it or not.

## Internal Architecture
```plaintext
[ Python Process (Asyncio Loop) ]
                                  |
         +------------------------+------------------------+
         |                        |                        |
         v                        v                        v
[ Task 1: Telegram Bot ]  [ Task 2: Scraper Engine ]  [ Task 3: Analytics (LLM) ]
         |                        |                        |
         +------------------------+------------------------+
                                  |
                                  v
                        [ SQLite / JSON Files ]
```
