#!/usr/bin/python
# -*- coding: utf-8 -*-

# API calls to interact with API.AI (Google Assistant / Actions / Home, Facebook Messenger)

from google.appengine.ext import ndb
from models import Habit, HabitDay, Task, Goal, User
from datetime import datetime, time
import random
from constants import HABIT_DONE_REPLIES, HABIT_COMMIT_REPLIES, SECURE_BASE, \
    JOURNAL
from google.appengine.api import urlfetch
import json
import tools
import re
import logging
import random
import imp
try:
    imp.find_module('secrets')
except ImportError:
    import secrets_template as secrets
else:
    import secrets

AGENT_GOOGLE_ASST = 1
AGENT_FBOOK_MESSENGER = 2

HELP_TEXT = "With the Flow agent, you can setup and review goals, top tasks each day, and habits to build. You can also set up daily journals to track anything you want."


class ConversationAgent(object):

    COMPLY_BANTER = [
        "Sure",
        "No problem",
        "Of course",
        "Absolutely",
    ]

    def __init__(self, type=AGENT_GOOGLE_ASST, user=None):
        self.type = type
        self.user = user

    def _quick_replies(self, buttons):
        '''
        Buttons are (title, payload) tuples
        '''
        return {
            "quick_replies": [{
                "content_type": "text",
                "title": b[0],
                "payload": b[1]
            } for b in buttons]
        }

    def _comply_banter(self):
        return random.choice(ConversationAgent.COMPLY_BANTER)

    def _user_disconnect(self):
        if self.type == AGENT_FBOOK_MESSENGER:
            self.user.fb_id = None
            self.user.put()
        elif self.type == AGENT_GOOGLE_ASST:
            self.user.g_id = None
            self.user.put()
        return "Alright, you're disconnected."

    def _journal(self):
        settings = tools.getJson(self.user.settings, {})
        questions = settings.get('journals', {}).get('questions', [])
        local_time = self.user.local_time()
        hr = local_time.hour
        in_journal_window = hr >= JOURNAL.START_HOUR or hr < JOURNAL.END_HOUR
        if in_journal_window:
            return "Please visit flowdash.co to submit today's journal"
        else:
            if questions:
                text = "You have %d journal questions setup: %s" % ' and '.join([q.get('text') for q in questions])
                text += ". You can submit your report after %s:00" % JOURNAL.START_HOUR
                return text
            else:
                # TODO
                return "Please visit flowdash.co to set up journal questions"

    def _goals_request(self):
        goals = Goal.Current(self.user, which="month")
        if goals:
            g = goals[0]
            if g.annual():
                speech = "Goals for %s. " % g.date.year()
            else:
                speech = "Goals for %s. " % datetime.strftime(g.date, "%B %Y")
            if g.text:
                for i, text in enumerate(g.text):
                    speech += "%d: %s. " % (i+1, text)
            else:
                speech = "No goals yet"
            return speech

    def _tasks_request(self):
        tasks = Task.Recent(self.user)
        tasks_undone = []
        n_done = Task.CountCompletedSince(self.user, datetime.combine(datetime.today(), time(0,0)))
        for task in tasks:
            if not task.is_done():
                tasks_undone.append(task.title)
        text = "You've completed %d %s for today." % (n_done, tools.pluralize('task', n_done))
        if tasks_undone:
            text += " You still need to do '%s'." % (' and '.join(tasks_undone))
        return text

    def _add_task(self, task_name):
        task = Task.Create(self.user, task_name)
        task.put()
        return self._comply_banter() + ". Task added."

    def _add_habit(self, habit):
        h = Habit.Create(self.user)
        h.Update(name=habit)
        h.put()
        return self._comply_banter() + ". Habit '%s' added." % habit

    def _habit_report(self, habit_param_raw):
        handled = False
        speech = None
        if habit_param_raw:
            habits = Habit.Active(self.user)
            for h in habits:
                if habit_param_raw.lower() in h.name.lower():
                    # TODO: Timezone?
                    done, hd = HabitDay.Toggle(h, datetime.today().date(), force_done=True)
                    encourage = random.choice(HABIT_DONE_REPLIES)
                    speech = "%s '%s' is marked as complete." % (encourage, h.name)
                    handled = True
                    break
            if not handled:
                speech = "I'm not sure what you mean by '%s'." % habit_param_raw
        else:
            speech = "I couldn't tell what habit you completed."
        return speech

    def _habit_commit(self, habit_param_raw):
        handled = False
        speech = None
        if habit_param_raw:
            habits = Habit.Active(self.user)
            for h in habits:
                if habit_param_raw.lower() in h.name.lower():
                    # TODO: Timezone?
                    hd = HabitDay.Commit(h, datetime.today().date())
                    encourage = random.choice(HABIT_COMMIT_REPLIES)
                    speech = "You've committed to '%s' today. %s" % (h.name, encourage)
                    handled = True
                    break
                else:
                    print habit_param_raw, "not in", h.name
            if not handled:
                speech = "I'm not sure what you mean by '%s'. You may need to create a habit before you can commit to it." % habit_param_raw
        else:
            speech = "I couldn't tell what habit you want to commit to."
        return speech

    def _habit_status(self):
        habits = Habit.All(self.user)
        today = datetime.today().date()
        habitday_keys = [ndb.Key('HabitDay', HabitDay.ID(h, today), parent=self.user.key) for h in habits]
        habitdays = ndb.get_multi(habitday_keys)
        n_habits_done = 0
        habits_committed_undone = []
        habits_done = []
        for hd in habitdays:
            if hd:
                habit = hd.habit.get()
                if hd.committed and not hd.done:
                    if habit:
                        habits_committed_undone.append(habit.name)
                if hd.done:
                    habits_done.append(habit.name)
                    n_habits_done += 1
        if n_habits_done:
            text = "Good work on doing %d %s (%s)!" % (n_habits_done, tools.pluralize('habit', n_habits_done), ' and '.join(habits_done))
        else:
            text = "No habits done yet."
        if habits_committed_undone:
            text += " Don't forget you've committed to %s." % (' and '.join(habits_committed_undone))
        return text

    def _status_request(self):
        habit_text = self._habit_status()
        address = "Alright %s." % self.user.first_name() if self.user.name else ""
        task_text = self._tasks_request()
        speech = ' '.join([address, task_text, habit_text])
        return speech

    def respond_to_action(self, action, parameters=None):
        speech = None
        data = {}
        if self.user:
            if action == 'input.disconnect':
                speech = self._user_disconnect()
            elif action == 'input.status_request':
                speech = self._status_request()
            elif action == 'input.goals_request':
                speech = self._goals_request()
            elif action == 'input.habit_report':
                speech = self._habit_report(parameters.get('habit'))
            elif action == 'input.habit_commit':
                speech = self._habit_commit(parameters.get('habit'))
            elif action == 'input.task_add':
                speech = self._add_task(parameters.get('task_name'))
            elif action == 'input.task_view':
                speech = self._tasks_request()
            elif action == 'input.habit_add':
                speech = self._habit_add(parameters.get('habit'))
            elif action == 'input.habit_status':
                speech = self._habit_status()
            elif action == 'input.journal':
                speech = self._journal()
            elif action == 'input.help_goals':
                HELP_GOALS = "You can set and review monthly and annual goals. Try saying 'set goals' or 'view goals'"
                speech = '. '.join([self._comply_banter(), HELP_GOALS])
                data = self._quick_replies([("Learn about Tasks", "input.help_tasks")])
            elif action == 'input.help_tasks':
                HELP_TASKS = "You can set and track top tasks each day. Try saying 'add task finish report' or 'my tasks'"
                speech = '. '.join([self._comply_banter(), HELP_TASKS])
                data = self._quick_replies([("Learn about Habits", "input.help_habits")])
            elif action == 'input.help_habits':
                HELP_HABITS = "You can set habits to build, and track completion. Try saying 'new habit', 'habit progress', or 'commit to run tonight'"
                speech = '. '.join([self._comply_banter(), HELP_HABITS])
                data = self._quick_replies([("Learn about Journals", "input.help_journals")])
            elif action == 'input.help_journals':
                HELP_JOURNALS = "You can set up daily questions to track anything you want over time. Try saying 'daily report'"
                speech = '. '.join([self._comply_banter(), HELP_JOURNALS])
            elif action == 'GET_STARTED':
                speech = "Welcome to Flow! " + HELP_TEXT
                data = self._quick_replies([("Learn about Goals", "input.help_goals")])
            elif action == 'input.help':
                speech = HELP_TEXT
                data = self._quick_replies([("Learn about Goals", "input.help_goals")])
        else:
            speech = "To get started, please link your account with Flow"
            if self.type == AGENT_FBOOK_MESSENGER:
                data = {
                    "attachment": {
                        "type": "template",
                        "payload": {
                            "template_type": "button",
                            "text": speech,
                            "buttons": [
                                {
                                    "type": "account_link",
                                    "url": SECURE_BASE + "/app/fbook/auth"
                                }
                            ]
                        }
                    }
                }
        return (speech, data)

    def _process_pattern(self, pattern):
        return tools.variable_replacement(pattern, {
            'HABIT_PATTERN': '(?P<habit>[a-zA-Z ]+)',
            'TASK_PATTERN': '(?P<task_name>[a-zA-Z ]{5,50})',
            })

    def parse_message(self, message):
        PATTERNS = {
            r'(?:what are my|remind me my|tell me my|monthly|current|my|view) goals': 'input.goals_request',
            r'(how am i doing|my status|tell me about my day)': 'input.status_request',
            r'(?:how do|tell me about|more info|learn about|help on) (?:tasks)': 'input.help_tasks',
            r'(?:how do|tell me about|more info|learn about|help on) (?:habits)': 'input.help_habits',
            r'(?:how do|tell me about|more info|learn about|help on) (?:journals|journaling|daily journals)': 'input.help_journals',
            r'(?:how do|tell me about|more info|learn about|help on) (?:goals|monthly goals|goal tracking)': 'input.help_goals',
            r'(?:mark|set) [HABIT_PATTERN] as (?:done|complete|finished)': 'input.habit_report',
            r'(?:add habit|new habit|create habit) [HABIT_PATTERN]': 'input.habit_add',
            r'(?:i finished|completed) [HABIT_PATTERN]': 'input.habit_report',
            r'(?:commit to|promise to|i will|planning to|going to) [HABIT_PATTERN] (?:today|tonight|this evening|later)': 'input.habit_commit',
            r'(?:my habits|habit progress|habits today)': 'input.habit_status',
            r'(?:add task|set task|new task) [TASK_PATTERN]': 'input.task_add',
            r'(?:my tasks|view tasks)': 'input.task_view',
            r'(?:help me|how does this work|what can i do|what can I say)': 'input.help',
            r'^(help|\?\?\?$)': 'input.help',
            r'(?:daily report|daily journal)': 'input.journal',
            r'^disconnect$': 'input.disconnect'
        }
        action = None
        parameters = None
        for pattern, pattern_action in PATTERNS.items():
            m = re.search(self._process_pattern(pattern), message, flags=re.IGNORECASE)
            if m:
                action = pattern_action
                if m.groupdict():
                    parameters = m.groupdict()
                break
        return (action, parameters)


class FacebookAgent(ConversationAgent):

    REQ_UNKNOWN = 1
    REQ_MESSAGE = 2
    REQ_POSTBACK = 3

    def __init__(self, request, type=AGENT_FBOOK_MESSENGER, user=None):
        super(FacebookAgent, self).__init__(type=type, user=user)
        self.body = tools.getJson(request.body)
        logging.debug(self.body)
        self.message_data = {}
        self.reply = None
        self.md = {}  # To populate with entry.messaging[0]
        self.request_type = FacebookAgent.REQ_UNKNOWN
        if not self.user:
            self._get_fbook_user()
        logging.debug("Authenticated user: %s" % self.user)
        self._get_request_type()
        self._process_request()

    def _link_account(self, psid, account_linking):
        status = account_linking.get('status')
        if status == 'linked':
            authcode = account_linking.get('authorization_code')
            user_id = authcode
            logging.debug("Linking user: %s" % authcode)
            self.user = User.get_by_id(int(user_id))
            if self.user and psid:
                self.user.fb_id = psid
                self.user.put()

    def _get_fbook_user(self):
        entry = self.body.get('entry', [])
        if entry:
            messaging = entry[0].get('messaging')
            if messaging:
                self.md = md = messaging[0]
                account_linking = md.get("account_linking", {})
                sender = md.get('sender', {})
                self.fb_id = psid = sender.get('id')
                if account_linking:
                    # Handle account linking
                    self._link_account(psid, account_linking)
                if not self.user and psid:
                    self.user = User.query().filter(User.fb_id == psid).get()
        else:
            logging.debug("malformed")

    def _get_request_type(self):
        if 'message' in self.md:
            self.request_type = FacebookAgent.REQ_MESSAGE
        elif 'postback' in self.md:
            self.request_type = FacebookAgent.REQ_POSTBACK

    def _get_fbook_message(self):
        return self.md.get('message', {}).get('text')

    def _process_request(self):
        '''
        Populate self.reply and self.data
        '''
        # TODO: Memcache state (string & param, e.g. set goals)
        if self.request_type == FacebookAgent.REQ_MESSAGE:
            message = self._get_fbook_message()
            if message:
                action, parameters = self.parse_message(message)
                if action:
                    self.reply, self.message_data = self.respond_to_action(action, parameters=parameters)
        elif self.request_type == FacebookAgent.REQ_POSTBACK:
            payload = self.md.get('postback', {}).get('payload')
            self.reply, self.message_data = self.respond_to_action(payload)

    def send_response(self):
        logging.debug("Reply: %s, User: %s, Message data: %s" % (self.reply, self.user, self.message_data))
        if self.fb_id and (self.reply or self.message_data):
            message_object = {}
            if self.reply and 'attachment' not in self.message_data:
                message_object['text'] = self.reply
            if self.message_data:
                message_object.update(self.message_data)
            body = {
                "recipient": {
                    "id": self.fb_id
                },
                "message": message_object
            }
            logging.debug(body)
            url = "https://graph.facebook.com/v2.6/me/messages?access_token=%s" % secrets.FB_ACCESS_TOKEN
            if tools.on_dev_server():
                logging.debug("Not sending request, on dev")
            else:
                response = urlfetch.fetch(url,
                                          payload=json.dumps(body),
                                          headers={"Content-Type": "application/json"},
                                          method="POST")
                logging.debug(response.status_code)
                if response.status_code != 200:
                    logging.warning(response.content)
            return body


