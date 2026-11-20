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
| **People** | Your people ranked, with labels and pins; circles of 5, 15, 50 and 150; a profile page for anyone; every conversation by volume; the automated senders that were set aside |
| **Search** | Find messages by words, phrases and filters; saved topics |
| **Patterns** | Over time, replies and read times, reactions, tone, words and phrases, expressions and style, "we" against "I", and what gets shared |
| **Reading** | Optional. AI reads every conversation and writes a digest of each; read them back person by person, in date order |
| **Ask AI** | Optional. Send a selection of messages to Claude with a question, after a preview and your confirmation |
| **Setup** | Update or quit the app, connect an AI tool, see what your Mac records, and what has been shared with AI |

**Update messages**, at the top right, brings in new messages without restarting anything.

### Tell it who matters: labels and pins

Messages cannot show who you live with or see every day, so the people closest to you often text you the least. No formula can fix that from messages alone. So:

- **Pin** the people in your inner circle. They are always listed first, whatever their score.
- **Label** people as family, close friend, friend or work.
- Label a business, delivery number or verification sender as **service** to hide it everywhere. Most are found and hidden for you; see below.

Home asks you to pin your inner circle the first time. Labels and pins are kept in their own file and survive every update.

### Codes, alerts and marketing are set aside

One-time codes, bank and card alerts, delivery notices and marketing texts are not people, and they crowd out the ones who are. The app finds these senders by itself and hides them from every screen, from anything sent to AI, and from what connected AI tools can search.

- **Hidden automatically:** short codes (numbers of 3 to 6 digits, which only automated services use), and senders you have never really written to whose messages mostly look automated: verification codes, account alerts, "Reply STOP" wording, do-not-reply notices. Answering with one word such as STOP or YES does not count as writing to someone.
- **Your call:** senders with weaker signs are listed under **People, Hidden senders** as "probably junk", with one button to hide them all.
- **Nothing is deleted.** Hidden messages stay in your local database. **Bring back** restores a sender, and ticking **Include automated senders** on any screen with filters shows everything, which is how you find an old code.
- **You always win:** anyone you pin or label as a person is never hidden, and anyone you label as a service always is. Group chats are never hidden.

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

Counting cannot tell you what a conversation was about, how it felt, or what was left unresolved. Reading can. The **Reading** section does it in three layers: a digest of every stretch of conversation (the full read), a check of how accurate those digests are, and a history of each relationship written from them.

### Histories

The story of you and one person, written by Claude Opus from every digest of your conversation: how it began, its eras, what you keep coming back to, friction and repair, what each of you gives and asks for, turning points, strengths, hard truths, and what looks unresolved.

- **Every point shows its evidence.** The numbers under a point open the digest it rests on, and that opens the messages themselves. A history is a model's reading of other models' digests, two steps from what was said, so check anything that surprises you.
- **Who gets one.** The list starts with everyone you have pinned, then the 25 people you have exchanged the most messages with. Add or remove anyone.
- **How it is written.** A long relationship is written year by year first, then as a whole, so it comes back in two rounds. A short one is written in one step.
- **What is sent.** Digests only. The messages themselves are not sent again.
- **What it cannot know.** Anything that was never texted. People who live together text mostly about logistics, and the model is told not to mistake that for distance.
- **The cap.** $40, planned for at twice the estimate. Nobody is written twice unless you throw their history away.

### Accuracy check

How far can the digests be trusted? Claude Opus re-reads a random sample of 150 digests against the original messages and reports any that say something the conversation does not support, or leave out something that matters. Each flagged digest is listed with what it said, what the conversation actually shows, and the messages, so you can check the checker. A third of the sample comes from the people read by Opus, where it is the same model checking itself, a weaker test. The cap is $10.

### Full read

Every conversation in your history, one-to-one and group, is read and given a digest: a summary, topics, how it read emotionally, what happened, any friction and how it ended, promises made, and people and places named.

- **Who reads what.** Claude Sonnet reads everything, except the people you name on the screen, who are read by Claude Opus. Type a name exactly as it appears in the app.
- **What is left out.** Automated senders (codes, alerts, marketing), and exchanges too short to say anything: under 5 messages and under 40 words.
- **How it runs.** Conversations are sent to Anthropic in batches, which are processed at half price, usually within an hour and at most within a day. You can quit the app while a batch is being processed. Open it again later and it collects the results and sends the next wave by itself.
- **The cap.** $150. Conversations go out in waves sized so that even if every one still being read cost twice the estimate, the total would stay under the cap. The true cost of each wave is recorded before the next is sized.
- **Order.** The people read by Opus go first, then the busiest conversations, so a stop or the cap falls on the quietest ones.
- **Nothing is paid for twice.** Digests from the pilot are reused. A conversation already read is never sent again, and new messages you bring in later only cause the new stretches to be read.

### Digests

One person or group at a time, in date order: the story of the conversation, stretch by stretch. Each digest can show the messages behind it. A digest is one model's reading, so open the messages beside anything that surprises you.

### Pilot and Pilot review

The pilot is how the model was chosen. Three Claude models (Haiku, Sonnet and Opus) each read the same sample of conversations; the pilot screen shows what each cost and what reading everything would cost with each. Pilot review shows the original conversation beside the three digests without saying which model wrote which, if you want to compare them yourself. The pilot has its own $30 cap.

Safeguards that apply to all of it:

- Nothing is sent until you have seen exactly what would be sent and pressed the button.
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
