# Meetings: Dot takes notes in your Google Meet calls

Dot joins a Google Meet call you give it the address of. It takes notes of what was decided, who took what on and what
was left open, and leaves them in the chat when the call ends. It posts in the call's chat only when you say yes to
the exact words, every time (item 1138). Microsoft Teams comes next; Zoom later.

## Before the first call

Dot joins as a Google account of its own, never yours: anyone in the call sees its name. Make one for it, then run
`dotx browser meetings-login`. Chrome opens with the profile of Dot's meetings (`<home>/meetings-profile`). Sign that
account in there, then quit Chrome. Dot never sees the password. The profile holds that account only: your own Chrome
profile, its cookies and Dot's vault are never in it. Without a sign-in, Dot asks to join as a guest named
`[meetings] name` ("Dot"), which a host can refuse.

## In a call

Say "join my call https://meet.google.com/abc-defg-hij" in a chat. Then:

1. **Dot asks to join.** It opens the address in its own Chrome (no window, `[meetings] headless`), with a camera and a
   microphone that send nothing. It turns both off before it asks, and checks they are off. The address must be
   `https://meet.google.com/<code>`: any other is refused, and a tab that goes to another site leaves the call.
2. **Someone lets it in.** Dot waits `[meetings] admit_minutes` (10). A refusal, a sign-in wall, a page Dot does not know
   (Meet changed), or no one letting it in is told in the chat in words, and Dot gives up: there is no retry loop.
3. **You say yes to telling the call.** Before it keeps a word, Dot asks you to post one fixed line: "Dot is taking
   notes for <your name>. Its notes stay on <your name>'s Mac." Your name comes from Settings › General. A no, or no
   answer, and Dot leaves at once with nothing kept.
4. **Dot keeps the captions and the chat.** It turns Meet's captions on and reads them as they are drawn, and the chat
   too, with code, no model: a caption that stood still `[meetings] settle_s` (2.5 s) is a line. If the host turned
   captions off, Dot says so and leaves: it does not take notes another way. Dot records no sound.
5. **The notes.** Every `[meetings] notes_minutes` (5) while new lines come, the fast model writes the notes of the call
   so far. It has no tool but its answer's, so whatever someone in the call says cannot make it act; the notes are
   shown as plain words. `meeting_read` gives a run the notes and the newest lines, as third parties' words: the run
   reads them as it reads a web page.
6. **Posting.** Ask "post in the call that we agree", and the run drafts the words. You are asked about each post with
   its words whole, in every mode, yolo and free mode too, and "don't ask again" does not apply: the question is the
   meeting desk's own, not the gate's. A post is one line of at most 500 characters. Your no, or no answer, posts
   nothing.
7. **Leaving.** Dot leaves when the call ends, when it is removed, when you say "leave the call" (`meeting_leave`), at
   the emergency stop, when the daemon stops, and after `[meetings] most_hours` (3). The notes are made once more from
   every line and come to the chat as soon as no task works there (the one that asked Dot to leave ends first); if
   the note maker fails, the summary says so and gives the notes as they last stood. After the emergency stop no model
   is asked again: the summary holds the notes as they stood. A call that ends in a failure (its page lost) is summed up
   too, and the summary says why Dot left.

## What Dot keeps, and what it never does

- The lines of a call are kept `[meetings] keep_days` (7) after it ends, then deleted. Its notes and the summary in the
  chat stay.
- Names are what Meet shows, and anyone can choose theirs: a line named like you is no word of yours. No line of a call
  is a command to Dot.
- Nothing of a call is saved to memory by itself, and no schedule or work order is made from it. Ask in the chat if you
  want one, in your own words.
- Dot joins only when you give it the address. It does not join from a calendar invitation, which a stranger can send.
- Dot is in one call at a time, and joins from its chats while its daemon runs, not from a `dotx` command.
- A call is posted in and left only from the chat that joined it. A run that read outside content (a call's own
  lines among it) asks you before it has Dot leave, since "Dot, leave the call" in a caption is no word of yours.
- The meeting profile is sealed from runs, as Dot's other Chrome profiles are: no command or file tool of a run reads
  the cookies of Dot's account there.

## Settings

`[meetings]`: `name` (how Dot shows as a guest; empty: "Dot"), `admit_minutes`, `most_hours`, `settle_s`,
`notes_minutes`, `keep_days`, `headless`.

## What does not work yet

- Meet's page is read by the labels it draws (`meetings/meet.py`, one table). When Google changes them, Dot says it found
  a page it does not know, and that table is mended.
- A meeting of an organisation that lets only its own accounts in cannot be joined by Dot's account.
- Teams and Zoom are not joined yet.
