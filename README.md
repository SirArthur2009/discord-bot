# SirArthur's Discord Bot

## Info

This is a program written in Python, it is for managing your discord channel, mainly setting up polls and general help. I **do not** recommend using this bot as is. It is for beginner based code (because I'm a beginner) and to help other people to understand. This bot, as is, is only useful to me and the users of my discord server.

## Specifications

- Python 3.x
- Discord Server (to hook it up to)
- Something to run the code on, (your computer, cloud, etc.)

## Setting it up

You have to pass in the following variable list. They will be retreive via `os.getenv()`

- TOKEN
  > The token to your discord bot
- CHANNEL_ID
  > The channel for which the poll and all runs
- NOTIFY_THREAD_ID
  > The thread that the notification message gets post on when poll requirement is met
- NOTIFY_ROLE_ID
  > ID of those who get notified when poll requirements are met and message is sent.
- VOTE_THRESHOLD
  > Number of votes required to send the notification message, includes the bot's vote
- LOGIN_CREDENTIALS
  > The credentials posted when the poll is stop and !running is called
- NOTIFIED_ROLE_ID
  > Role ID that gets called when !running is called
- GENERAL_CHANNEL_ID
  > The channel in which you can subscribe to get the NOTIFIED_ROLE_ID
- POLL_PAUSE_TIME
  > Time the poll pauses processes (24 hour format)
- POLL_RESUME_TIME
  > Time the poll resumes processes (24 hour format)

## What I do

I use <a href="https://www.railway.com">railway</a> to host my discord bot, it has little enough traffic that it is free. Maybe after it has tons of of traffic and does complicated tasks, i will either 1. Host it myself or 2. Buy a place to host it. It works surprising well.

## What it does

1. Posts a poll in CHANNEL_ID channel
1. Watches for reaction adds, once the reactions count has reached the VOTE_THRESHOLD it will send a message on the NOTIFY_THREAD_ID notifying all with the NOTFIY_ROLE_ID
1. Watchs the GENERAL_CHANNEL_ID for the commands !getnotified and !stopnotified and assigns and takes away roles accordingly.
1. Pauses at POLL_PAUSE_TIME and continues at POLL_RESUME_TIME

## All bot commands

- !resetpoll
  > This commands resets the poll. Used to clear running mode and clear the paused mode.
- !running
  > This command puts it into running mode, it mentions NOTIFIED_ROLE_ID and shows the credentials to log in
- !pause
  > This pauses the processes, shows a pause message
- !unpause
  > This unpauses the processes.
- !getnotified
  > This adds the role of NOTIFIED_ROLE_ID to the person that runs it. (This has to be run in GENERAL_CHANNEL_ID)
- !stopnotified
  > This removes the role of NOTIFIED_ROLE_ID from the person that runs it. (This has to be run in GENERAL_CHANNEL_ID)
