# What the robot can do

Everything this fork's robot (an M5Stack StackChan running `firmware-stackchan/`, with the brain in
`server/`) can do today, by what you do or say. How to build and set it up is in [README.md](README.md) and
[docs/stackchan.md](docs/stackchan.md). **Kept up to date:** every change that adds, changes or removes a
feature updates this file in the same commit.

Phrases in quotes are examples: anything close works, and most commands only count when they're (nearly) all
you said. Anything else goes to the language model, which can do the same things with its tools.

## Waking, talking and sleeping

- **Wake it** by name ("hey Rocky", "Rocky chan"), by a wake word of another personality (which also switches
  to it), or by a generic wake word that wakes whoever is in use (Settings; "computer, robot, bot" here). It
  looks up and answers. Double-tapping its head wakes it too.
- **Talk normally** for a minute after that (Settings: awake time); every turn keeps it awake. It decides
  you've finished the way a person does (Smart Turn), not after a fixed pause, and ignores coughs and "hmm".
- **Replies** are short (up to `REPLY_MAX_SENTENCES`, 3), spoken sentence by sentence while the rest is still
  being written, with an expression on its face and body language: small head moves, a glance up while it
  thinks, the light bars.
- **Stop it mid-reply** by holding a hand over the sensor right of the camera (Settings: near_stop).
- **"Go to sleep"**, "goodnight", "stop listening": it says goodnight and only wakes for a wake word. Holding a
  hand on its head for 3 s tucks it in too. It dozes off by itself when you stop talking.
- **"Power off"**: it asks for a yes, then powers off. The side power button turns it back on.
- Speech-to-text runs on the brain's computer (Parakeet; Whisper or Moonshine as options). The robot can
  instead listen for "Computer" itself (Settings: wake engine "robot").

## Seeing

- **"What do you see?"**, "what am I holding?", "read this": a camera picture goes with any question about
  seeing, and with follow-ups for a minute after ("how about this one?"). It describes only what's there.
- **"Look left"**, "look up a bit", "look here": it turns or nods its head (or stays put for "here"), takes a
  fresh picture and says what it sees. It looks around by itself when what you ask about is out of view.
- **"Track me"** / "stop tracking": its head follows your face.
- **"Find me"**, "where am I", "can you see me": it sweeps its head slowly side to side, then a little higher,
  until it sees a face, then follows it. It says so if it finds no one. (It stops at the first face, not
  necessarily yours.)
- **"Center and relax"**, "look straight ahead": tracking off, head back to the middle.

## People, voices and memories

- **It recognises voices** it has learned and knows who is talking ("this sounds like Sam"). A short turn
  goes to whoever was just talking if the voice fits.
- **"Learn Sam's voice"**: it interviews that person with questions until it has about 40 s of their speech
  (5 turns at least). "Forget Sam's voice" removes it.
- **"Remember that ..."** / "forget that ...": facts it keeps for good. Each is about one person (whoever is
  talking, unless you say) or everyone, and known by every personality or just the one that heard it ("only
  you"). The live view's Memories tab lists them by person and edits or forgets them.

## Help around the desk

- **Timers**: "set a timer for 10 minutes for the tea", "cancel the tea timer", "cancel all timers". It wakes
  to say when one is done, and they survive a brain restart (a missed one is said as missed, with the time).
- **Weather**: "what's the weather?", "will it rain in Paris tomorrow?" (Open-Meteo, free; home is a setting).
- **Looking things up**: news, facts, opening hours (Anthropic web search, about a cent a question).
- **Your online store** (when a store API is set up, `STORE_API`): "any orders to ship today?", "what's on my
  plate?", "any buyer messages?", "how were sales this week?", "how many watchers on the laser?". It only
  reads: changes stay with you at the computer.
- **Its own state**: "how's your battery?", "how hot are you?".
- **"Dance!"**: it dances, with a rainbow on the light bars.

## Personalities

- Rocky (the Eridian from *Project Hail Mary*) is built in; others are made in the live view's Personalities
  tab, each with its own description, example replies, set lines, voice (any Piper voice, including your own
  trained ones in `server/data/voices`), face pack and wake words. "Write it for me" drafts one from a
  sentence.
- **Fish Audio voices**: a personality can also have a Fish Audio voice (a fish.audio voice's link or id),
  used once `FISH_AUDIO_API_KEY` is set; its Piper voice speaks if Fish can't (no internet, no credit).
  `FISH_MODEL` picks Fish's model (s2.1-pro-free is free under fair use; s2.1-pro is $15 per million bytes of
  text). Test voice in the Personalities tab plays it.
- **Switch** by asking ("let me talk to Juniper"), by that personality's wake word, from the live view, or in
  the console (`character <id>`). The default one is a setting; a brain restart comes back as it.
- Rocky worries about your sleep, food and health: that's in character.

## Touch (the strip on top of its head)

- **Tap**: a fist bump. **Double tap**: wakes it, like its name.
- **Stroke** it: it leans into your hand and likes it; several quick strokes (a scratch) delight it.
- **Rest your hand** there: it goes content, eyes half shut; still there after 3 s, it says goodnight and
  sleeps.

## On its own

- **Speaks up** for timers, a low battery (20%, then more often at 10% and 5%) and heat (above 70 °C inside),
  waiting until any conversation has finished.
- **Battery**: on battery it sleeps deeper after 5 minutes asleep, powers off after 30 minutes asleep (unless
  a timer is set) and shuts down at 5%. USB turns it back on. A power-bank setting keeps it from powering off
  on a long quiet.
- **Without a brain** for 3 minutes it goes to sleep, and reconnects by itself.
- **While asleep on USB** the screen shows a clock with the weather, or a game that plays itself (Pong,
  Tetris, Frogger, Game of Life), or roam (the sleeping face looking round the room). In a dark room (its
  light sensor) the screen dims or goes dark (night mode).
- **Idle**: it glances round attentively; its light bars drift through the face's colour. Asleep, they're off
  or a Cylon scanner.
- **Light bars** show what it's doing: breathing while it listens, a sweep while it thinks, a level meter
  while it talks, flashes for alerts (timer, battery, heat, hush).
- **Looks after itself**: its chip bus heals itself, the camera restarts if it stalls, and the servos go limp
  in deep sleep.

## The live view (<http://127.0.0.1:8766/>, this computer only)

- **Live**: the camera, what it heard and said, controls for its head, face, voice, sleep, listening and power
  off, and a console.
- **Personalities**: make, edit, test, import, export and switch them.
- **Memories**: what it remembers, by person; add, edit and forget.
- **Settings**: your name, awake time, volume, body language, the hand-over-sensor stop, power bank, the
  default personality and face pack, clock and weather, screen brightness and night mode, the idle screen,
  the light bars, wake words and engine, listening thresholds and speaker tone. Changes apply at once and are
  kept.
- **Log**: the brain's log, live.
- An API for the same actions (`POST /api/<action>`), and a text console in the brain's window (`ask`, `say`,
  `volume`, `track`, `find`, `center`, `emo`, `face`, `character`, `dance`, `leds`, `memories`, `remember`,
  `forget`, `poweroff`, `status`...).

## Faces

- 12 expressions: neutral, happy, sad, angry, surprised, sleepy, thinking, curious, skeptical, excited, love,
  confused. It blinks and its eyes move.
- Face packs (`server/faces/`): rocky, bean, blocky, visor, stackchan, plus your own in `faces/custom/`; each
  personality can choose one.

## Updating

- Firmware updates over WiFi (OTA) or USB; the USB serial console drives the same commands as the brain.
- Tests for the brain run with no hardware (README.md, "Tests run with no hardware").
