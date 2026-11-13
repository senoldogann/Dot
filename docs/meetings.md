# Meetings: Dot in your Google Meet calls

Dot joins a Google Meet call you give it the address of, as Claude Meetings does. It tells the call it is there,
answers in the call's chat whoever speaks to it (item 1150), takes notes of what was decided, who took what on and what
was left open, and leaves them in the chat when the call ends (item 1138). A post a task asks for is put to you with
its words, every time. Microsoft Teams comes next; Zoom later.

## Before the first call

Dot joins as a Google account of its own, never yours: anyone in the call sees its name. Make one for it, then run
`dotx browser meetings-login`. Chrome opens with the profile of Dot's meetings (`<home>/meetings-profile`). Sign that
account in there, then quit Chrome. Dot never sees the password. The profile holds that account only: your own Chrome
profile, its cookies and Dot's vault are never in it. Without a sign-in, Dot asks to join as a guest named
`[meetings] name` ("Dot"), which a host can refuse.

## In a call

Say "join my call https://meet.google.com/abc-defg-hij" in a chat. Then:

1. **Dot asks to join.** Asked in your own words, it goes. Asked by a task or chat that read outside content (a mail, a
   page, an invitation), or in a chat Dot opened on its own, it asks you first, in every mode, free mode too: a link in
   a mail is no invitation. It opens the address in its own Chrome (no window, `[meetings] headless`), with a camera
   and a microphone that send nothing. It turns both off before it asks, and checks they are off. The address must be
   `https://meet.google.com/<code>`: any other is refused, and a tab that goes to another site leaves the call.
2. **Someone lets it in.** Dot waits `[meetings] admit_minutes` (10). A refusal, a sign-in wall, a page Dot does not know
   (Meet changed), or no one letting it in is told in the chat in words, and Dot gives up: there is no retry loop.
3. **Dot tells the call.** Once it is let in, before it keeps a word, Dot posts one fixed line: "Dot is taking notes
   for <your name> and answers what you ask it here. It is an AI: its answers are its own, not <your name>'s word. Its
   notes stay on <your name>'s Mac." With answers off it says only that it takes notes. Your name comes from Settings ›
   General.
4. **Dot keeps the captions and the chat.** It turns Meet's captions on and reads them as they are drawn, and the chat
   too, with code, no model, every half second: a caption that stood still `[meetings] settle_s` (1.5 s) is a line, a
   chat message is one at once. It keeps the chat open, as its messages are on the page only then. If the host turned
   captions off, Dot says so and leaves: it does not take notes another way. Dot records no sound.
5. **Dot answers whoever speaks to it** (item 1150, `[meetings] answers`, on). A line speaks to Dot when it starts with
   its name, after a greeting or two ("Dot, …", "Hey Dot …", "Selam Dot …"), ends on it ("what was the budget, Dot?"),
   or is a chat message with `@Dot`; someone who only says "Hey Dot" asks with their next line. "Dot com", "the dot",
   "Dot'un notları", "let's ask Dot" and a name further into a line speak of Dot, not to it (but "what is it like,
   Dot?", the name after a comma, speaks to it). The answer comes in the call's chat, in writing, in the language asked,
   without asking you, in every mode. It is written by the fast model with no tool, from
   the call's own lines and your and Dot's names: nothing of yours (no files, mail, calendar, memory, vault or task)
   reaches it, so whatever someone in the call says, all Dot can put in the call is what was said there and what the
   model knows of the world. A question that needs your things is answered with a line that you can ask Dot for it in
   its own chat. It never speaks for you (no promise, agreement, price or decision in your name). What holds in code:
   - an answer is one line of at most 400 characters, with no link, web, mail or IP address (no word.word with a last
     part of two letters or more, whatever the ending, but a few file endings such as .js or .pdf), and none that speaks
     to Dot (so that Dot does not answer itself, or another bot, for ever); such an answer is dropped and logged;
   - one answer at a time; one question waits per person and three in all, the same question said twice (or typed as
     well) is one, and one that waited a minute is dropped; at most 6 questions a minute and 60 a meeting go to the
     model, whatever comes of them, after which Dot answers no more there and its summary says so;
   - the lines and the answer are masked by the forms of secrets (keys, tokens) only, never by the values Dot holds:
     masking those would tell the call which of its guesses is one of your passwords;
   - Dot's own messages (Meet names them "You") and its answers are never questions;
   - each answer is a line of the meeting (kind `dot`): the notes, `meeting_read` and the summary see what Dot said.
   Set `[meetings] name` to the name Dot's account shows if it is not "Dot": it is the name a question starts with.
6. **The notes.** Every `[meetings] notes_minutes` (3) while new lines come, the fast model writes the notes of the call
   so far. It has no tool but its answer's, so whatever someone in the call says cannot make it act; the notes are
   shown as plain words. `meeting_read` gives a run the notes and the newest lines, as third parties' words: the run
   reads them as it reads a web page.
7. **Posting.** Ask "post in the call that we agree", and the run drafts the words: a task holds your tools and data,
   so this is not an answer of Dot's. You are asked about each post with
   its words whole, in every mode, yolo and free mode too, and "don't ask again" does not apply: the question is the
   meeting desk's own, not the gate's. A post is one line of at most 500 characters. Your no, or no answer, posts
   nothing.
8. **Leaving.** Dot leaves when the call ends, when it is removed, when you say "leave the call" (`meeting_leave`), at
   the emergency stop, when the daemon stops, and after `[meetings] most_hours` (3). The notes are made once more from
   every line and come to the chat as soon as no task works there (the one that asked Dot to leave ends first); if
   the note maker fails, the summary says so and gives the notes as they last stood. After the emergency stop, and when the
   daemon stops, no model is asked again: the summary holds the notes as they stood. A call that ends in a failure (its page lost) is summed up
   too, and the summary says why Dot left.

## What Dot keeps, and what it never does

- The lines of a call are kept `[meetings] keep_days` (7) after it ends, then deleted. Its notes and the summary in the
  chat stay.
- Names are what Meet shows, and anyone can choose theirs: a line named like you is no word of yours. No line of a call
  is a command to Dot.
- Nothing of a call is saved to memory by itself, and no schedule or work order is made from it. Ask in the chat if you
  want one, in your own words.
- Dot joins only when you give it the address. It does not join from a calendar invitation, which a stranger can send,
  and a task that read one asks you first (step 1).
- Dot is in one call at a time, and joins from its chats while its daemon runs, not from a `dotx` command.
- A call is posted in and left only from the chat that joined it. A run that read outside content (a call's own
  lines among it) asks you before it has Dot leave, since "Dot, leave the call" in a caption is no word of yours;
  in free mode it does not ask, and the meeting's record says a run that read outside content had Dot leave.
- The meeting profile is sealed from runs, as Dot's other Chrome profiles are: no command or file tool of a run reads
  the cookies of Dot's account there.

## Settings

`[meetings]`: `name` (how Dot shows as a guest, and the name a question to it starts with; empty: "Dot"), `answers`
(whether Dot answers whoever speaks to it; off, it only takes notes), `admit_minutes`, `most_hours`, `settle_s`,
`notes_minutes`, `keep_days`, `headless`.

## What does not work yet

- Meet's page is read by the labels it draws (`meetings/meet.py`, one table). When Google changes them, Dot says it found
  a page it does not know, and that table is mended.
- A meeting of an organisation that lets only its own accounts in cannot be joined by Dot's account.
- Teams and Zoom are not joined yet.
- A spoken question cut by a long pause ("Dot, what was the … budget for Q3?") is answered by its first half; the rest
  is a line of the call. Waiting while the speaker's caption is still open is the next item.
- Captions are in the language Meet gives Dot's account: a call held in Turkish needs that account's caption language
  set to Turkish in Meet, or the questions to Dot are not read right.
- A question that needs your tools or data (a figure from last year's sheet) is not done in the call yet: Dot says you
  can ask it in its own chat. Claude Meetings asks the person whose tools they are; that is the next item, with a live
  page of the meeting and a description of a shared screen. Faces are never read for feelings: the EU AI Act (article
  5(1)(f)) forbids it at work.
