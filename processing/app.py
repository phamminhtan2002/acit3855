import datetime
import json
import requests
import yaml
import logging
import logging.config
import uuid
import sqlite3
import os
import connexion
from apscheduler.schedulers.background import BackgroundScheduler
from stats import Stats
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from base import Base
from flask_cors import CORS, cross_origin
from connexion import NoContent

# Your functions here


def get_health():
    return NoContent, 200


def create_table(sql_path):
    conn = sqlite3.connect(sql_path)
    c = conn.cursor()

    c.execute('''
            CREATE TABLE IF NOT EXISTS stats
            (id INTEGER PRIMARY KEY ASC,
            num_ci_readings INTEGER NOT NULL,
            num_bc_readings INTEGER NOT NULL,
            max_numPeople INTEGER,
            max_numNights INTEGER,
            last_updated VARCHAR(100) NOT NULL)
    ''')

    conn.commit()
    conn.close()


def get_stats():
    '''Get all the Stats objects from the database in descending order'''
    logger.info("Requesting process has started")

    session = DB_SESSION()
    results = session.query(Stats).order_by(Stats.last_updated.desc()).all()
    session.close()

    results_list = []

    if results != []:
        for reading in results:
            results_list.append(reading.to_dict())
        logger.debug(results_list)
        logger.info("Requesting process has ended")
        return results_list[0], 200

    static_list = {
        "num_ci_readings": 0,
        "num_bc_readings": 0,
        "max_numPeople": 0,
        "max_numNights": 0,
        "last_updated": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    }

    logger.error("Statistics do not exist")
    logger.info("Requesting process has ended")
    return static_list, 404


def create_stats(body):
    '''write a new Stats object to the database'''

    session = DB_SESSION()

    stats = Stats(body["num_ci_readings"],
                  body["num_bc_readings"],
                  body["max_numPeople"],
                  body["max_numNights"],
                  datetime.datetime.strptime(body["last_updated"],
                                             "%Y-%m-%dT%H:%M:%S"))
    session.add(stats)
    session.commit()
    session.close()

    return NoContent, 201


def populate_stats():
    """ Periodically update stats """
    logger.info("Start Periodic Processing")
    logger.info("Checking new changes")

    currentStat = get_stats()

    body = currentStat[0]

    current_timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    getCheckinResponse = requests.get(
        app_config['eventstore']['url']+"/checkIn?start_timestamp=" +
        body["last_updated"] + "&end_timestamp=" +
        current_timestamp)

    trace = str(uuid.uuid4())

    checkinList = []
    bookingConfirmList = []

    if getCheckinResponse.status_code == 200:
        checkinList = getCheckinResponse.json()
        logger.info("Number of events received: %s" % (len(checkinList)))
        logger.debug(
            'Stored event check in request with a trace id of ' + trace)
    else:
        logger.error("Request data failed")

    getBookingConfirmResponse = requests.get(
        app_config['eventstore']['url']+"/bookingConfirm?start_timestamp=" +
        body["last_updated"] + "&end_timestamp=" +
        current_timestamp)

    trace = str(uuid.uuid4())
    if getBookingConfirmResponse.status_code == 200:
        bookingConfirmList = getBookingConfirmResponse.json()
        logger.info("Number of events received: %s" %
                    (len(bookingConfirmList)))
        logger.debug(
            'Stored event check in request with a trace id of ' + trace)
    else:
        logger.error("Request data failed")

    body['last_updated'] = current_timestamp
    if checkinList != []:
        body["num_ci_readings"] += len(checkinList)

        body["max_numPeople"] = max(
            item["numPeople"] for item in checkinList)

    if bookingConfirmList != []:
        body["num_bc_readings"] += len(bookingConfirmList)
        body["max_numNights"] = max(item["nights"]
                                    for item in bookingConfirmList)

    create_stats(body)

    logger.debug("Updated info: " + json.dumps(body))
    logger.info("Periodic Processing is ended")


def init_scheduler():
    sched = BackgroundScheduler(daemon=True)
    sched.add_job(populate_stats,
                  'interval',
                  seconds=app_config['scheduler']['period_sec'])
    sched.start()


app = connexion.FlaskApp(__name__, specification_dir="")
app.add_api("openapi.yaml", base_path="/processing",
            strict_validation=True, validate_responses=True)

if "TARGET_ENV" in os.environ and os.environ["TARGET_ENV"] == "test":
    print("In Test Environment")
    app_conf_file = "/config/app_conf.yml"
    log_conf_file = "/config/log_conf.yml"
else:
    print("In Dev Environment")
    app_conf_file = "app_conf.yml"
    log_conf_file = "log_conf.yml"
    CORS(app.app)
    app.app.config['CORS_HEADERS'] = 'Content-Type'

with open(app_conf_file, 'r') as f:
    app_config = yaml.safe_load(f.read())

with open(log_conf_file, 'r') as f:
    log_config = yaml.safe_load(f.read())
    logging.config.dictConfig(log_config)

logger = logging.getLogger('basicLogger')


sqliteUrl = "sqlite:///%s" % app_config["datastore"]["filename"]
logger.info(sqliteUrl)
DB_ENGINE = create_engine(sqliteUrl)
Base.metadata.bind = DB_ENGINE
DB_SESSION = sessionmaker(bind=DB_ENGINE)

if __name__ == "__main__":
    create_table(app_config["datastore"]["filename"])
    init_scheduler()
    app.run(port=8100, use_reloader=False)
