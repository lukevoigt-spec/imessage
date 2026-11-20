# iMessage Explorer

Search your own text message history, understand your relationships and patterns,
and optionally ask an AI questions about a selection of messages. Runs on your Mac.

## Install

You need a Mac signed into Messages with your history on it. Nothing else.

1. On this project's GitHub page, press the green **Code** button, then **Download ZIP**.
2. Open the downloaded folder and double-click **Double-click to install.command**.
3. The first time, macOS will say it cannot verify the file. Open **System Settings**, then **Privacy & Security**, scroll down, and press **Open Anyway**. (On older versions of macOS: right-click the file, choose **Open**, then **Open** again.) You only do this once.

The installer puts **iMessage Explorer** in your Applications folder and starts it. It never asks for a password.

From then on:

- **To open it**, open iMessage Explorer from Applications or Spotlight, like any app. A Terminal window opens alongside your browser; that window is the app running. You never type in it. Leave it open while you use the app.
- **To get a newer version**, go to **Setup** and press **Check for a newer version**, then **Update the app**. Nothing to download or replace.
- **To stop it**, go to **Setup** and press **Quit iMessage Explorer**, or close the Terminal window.
- **To bring in new messages**, press **Update messages** at the top right.

### If it cannot read your messages

macOS only lets apps you approve read Messages. If the installer says a setting is needed, it opens the right page for you: **System Settings, Privacy & Security, Full Disk Access**. Switch on **Terminal**, quit Terminal completely, then open iMessage Explorer from Applications.

### If macOS offers to install developer tools

Accept. That is how Apple supplies Python, which this app is written in. Then run the installer again.

### Comfortable with Terminal?

This one line does the same as steps 1 to 3:

```
curl -fsSL https://raw.githubusercontent.com/lukevoigt-spec/imessage/main/install.sh | bash
```

## Getting around

Seven sections across the top. Explanations of how each number is worked out sit behind a "How this is measured" link, so screens stay easy to scan.

| Section | What is there |
|---|---|
| **Home** | Your inner circle, what has changed lately, and where to go next |
| **People** | Your people ranked, with labels and pins; circles of 5, 15, 50 and 150; a profile page for anyone; every conversation by volume |
| **Search** | Find messages by words, phrases and filters; saved topics |
| **Patterns** | Over time, replies and read times, reactions, tone, words and phrases, expressions and style, "we" against "I", and what gets shared |
| **Reading** | Optional. Three AI models read a sample of your real conversations and write a digest of each; you mark them, blind, and see what a full read would cost |
| **Ask AI** | Optional. Send a selection of messages to Claude with a question, after a preview and your confirmation |
| **Setup** | Update or quit the app, connect an AI tool, see what your Mac records, and what has been shared with AI |

**Update messages**, at the top right, brings in new messages without restarting anything.

### Tell it who matters: labels and pins

Messages cannot show who you live with or see every day, so the people closest to you often text you the least. No formula can fix that from messages alone. So:

- **Pin** the people in your inner circle. They are always listed first, whatever their score.
- **Label** people as family, close friend, friend or work.
- Label a business, delivery number or verification sender as **service** to remove it from the rankings.

Home asks you to pin your inner circle the first time. Labels and pins are kept in their own file and survive every update.

## Privacy, in plain terms

- This project holds only code. **Your messages are never part of it.** They stay on your Mac.
- Apple's Messages database is only ever **read**. This tool cannot change or delete your messages.
- Your searchable copy is stored in `~/.imessage-explorer`, **outside this folder**, so message data cannot be committed to GitHub by accident.
- The interface is reachable only from this Mac, and only by the browser tab the tool opens.
- Message text leaves the Mac in exactly two cases: you confirm a **Reading** or **Ask AI** request, or an AI tool **you connected** runs a search. Both are listed, without the message text, on the Setup screen.
- The only other time the app uses the internet is when you press **Check for a newer version** or **Update the app**. That asks GitHub which version is published and downloads the code. Nothing about you or your messages is sent.
- Your messages include other people's words. Sharing them with an AI service shares their words too.

### The summary after bringing in messages

Each time messages are brought in, the app shows a summary. If it says **"messages had text this tool could not read"**, tell whoever maintains this tool. It means Apple's storage format differs from what the tool expects, and those messages are stored without their words.

## After an update

Some versions add new measurements. When that happens the app opens with "One update needed". Press the button and it re-reads your messages to add them. Nothing already brought in is lost, and your labels, pins and topics are kept. Expect roughly a minute per million messages.

## What your Mac records

Reactions, read times, direct replies, edits, unsends and attachments depend on your version of macOS and, for read times on messages you sent, on the other person sharing read receipts. The Setup screen shows what your Mac has, under "What this Mac records". Producing that report reads no message text. Where something is not recorded, the tool says so instead of showing a zero.

## How the deeper analysis works, and its limits

Every screen shows a plain definition of what was counted. The short version:

- **Tone** is scored on your Mac from a published word list (VADER). Positive words and emoji raise a message's score, negative ones lower it, "not" reverses, "very" strengthens. It reads words, not meaning. Sarcasm, teasing and inside jokes are missed, and most short messages are neutral. Trust the broad patterns across many messages, not the score of any one message. Plain acknowledgements such as "ok" and "yes" are treated as neutral.
- **Importance** is the plain average of six parts, each 0 to 100: volume, consistency over your whole history, recency, how two-sided it is, warmth, and depth. All six are shown for every person. It measures **texting only**. Someone you mostly see in person or call will score lower than they matter to you.
- **Community** counts steady contacts (messages in at least 6 of the last 12 months), how many of those are two-way, how spread out your messaging is, and compares each with a year earlier.
- **Balance** counts words written, runs of three or more messages in a row, questions asked, and questions left unanswered.
- **Reactions** are matched to the message they were placed on, so "given" and "received" mean what they say.
- **Read times** exist only where your Mac recorded them. The screen shows how much of your history that covers.
- **"We" against "I"** and **style matching** come from relationship research. The evidence is moderate and shows links, not causes. Treat them as curiosities to watch over time, not verdicts.
- **Circles** arrange your people in rings of 5, 15, 50 and 150, a pattern found in phone records. Your pins fill the rings first.
- **A fuller read**: none of the above understands what was said. For that, the Profile and Tone screens have a button that sets up an AI question about one relationship or period. It goes through the same preview-and-confirm step as every AI request.

## Names instead of phone numbers

Names come from the Contacts app on this Mac. If someone shows as a number, either add them to Contacts and press Update messages, or create `~/.imessage-explorer/contacts.csv` with one line per person:

```
+12815550101,Avery Stone
someone@example.com,Jordan Pike
```

## Search cheat sheet

| Type this | Finds messages with |
|---|---|
| `dinner friday` | both words, anywhere |
| `"date night"` | that exact phrase |
| `flight OR airport` | either word |
| `flight -work` | "flight" but not "work" |
| `vacat*` | words starting with "vacat" |

## Reading: have AI read your conversations (optional)

Counting cannot tell you what a conversation was about, how it felt, or what was left unresolved. Reading can. The **Reading** section runs a pilot:

1. Three Claude models (Haiku, Sonnet and Opus) each read the same sample of your real conversations: by default 40 stretches of conversation with each of the three people you messaged most in the last full year.
2. Each writes a digest: a summary, topics, how it read emotionally, what happened, any friction and how it ended, promises made, and people and places named.
3. On **Review digests** you read the original conversation beside the three digests and mark each one right, missing something, or wrong. You are not told which model wrote which, and the letters are shuffled for every conversation, so your marks are not swayed by a model's name.
4. The pilot screen then shows what each model cost and what reading your whole history would cost with each.

Safeguards:

- Nothing is sent until you have seen exactly what would be sent and pressed the button.
- A hard spending cap of $30. Before every request the app checks that even the dearest possible reply would still fit, and stops if not.
- A conversation that has been read is never paid for twice. You can stop and continue later.
- Digests and the record of spending are kept in their own file in `~/.imessage-explorer`, untouched by bringing in new messages.

## Your Anthropic key

Reading and Ask AI use the Anthropic API, which is **billed per use against the credit on your Anthropic account and is separate from a Claude subscription**.

The first time you open Reading or Ask AI, the app asks for a key: sign in at console.anthropic.com, create an API key, and paste it in. Nothing has to be installed and nothing is typed in Terminal. The key is stored in this Mac's Keychain, never in a file. Saving it sends no messages; it only asks Anthropic whether the key works.

## Ask AI (optional)

Ask a question about a selection of messages. A typical question over a few hundred messages costs a few cents, and the screen shows an estimate before anything is sent. One request is limited to 1,500 messages or 200,000 characters. If more match, the screen says how many are left out and you choose which to send or narrow the filters. If the AI declines a request, the service may retry it on its fallback model automatically.

## Connect an AI tool (optional)

AI tools that run **on this Mac** can search the local database through a standard connection called MCP. The Setup screen shows the exact lines to copy for Claude Code, Claude Desktop and Cursor. The connection is read-only and returns at most 50 messages per request.

A bot that runs in the cloud cannot use this, because the database never leaves the Mac. That is by design.

Anything a connected tool retrieves is sent on to that tool's AI service.

## Removing it

Drag iMessage Explorer from Applications to the Trash. To also remove its code, delete the folder `~/Library/Application Support/iMessage Explorer`. To remove your local database, labels and topics as well, delete the folder `~/.imessage-explorer`. Your real messages are untouched. To remove the API key, open the Keychain Access app, search for `imessage-explorer-anthropic`, and delete it.

## Command line (optional)

Everything above works without it. If you want it, run these from the installed code folder, `~/Library/Application Support/iMessage Explorer/app`:

```
python3 imessage_explorer.py check
python3 imessage_explorer.py search "dinner friday" --contact "Avery Stone" --from 2025-01-01
python3 imessage_explorer.py stats people
python3 imessage_explorer.py probe
```

To try the app on made-up data without touching real messages, run `python3 tests/make_demo.py demo 20000` from a downloaded copy and follow what it prints.

## For whoever maintains this

- Code is in `imx/`. No third-party packages. Requests to Anthropic are made directly by `imx/claude_api.py`, through the Mac's own `curl`, so nothing has to be installed.
- Tests: `python3 -m unittest discover -s tests`. They use only fictional data and never contact Anthropic.
- The `main` branch is the published version: the installer and the in-app update both install it. Do unfinished work on another branch, and raise the version number in `imx/__init__.py` when publishing. `imx/update.py` explains how updating works.
- The sentiment word list in `imx/data/vader_lexicon.txt` comes from VADER 3.3.2 by C.J. Hutto and Eric Gilbert, used under the MIT licence in `imx/data/VADER_LICENSE.txt`.
