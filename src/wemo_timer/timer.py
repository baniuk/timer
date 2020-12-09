import logging
import random
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import click
import pytz
import pywemo
import tenacity
from apscheduler.events import EVENT_JOB_EXECUTED, JobExecutionEvent
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.job import Job
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dynaconf import Dynaconf
from flask import Flask, jsonify
from pint import UnitRegistry
from tenacity.before_sleep import before_sleep_log
from tenacity.stop import stop_after_delay
from tenacity.wait import wait_fixed, wait_random
from werkzeug.exceptions import NotFound

_LOGGER = logging.getLogger(__name__)

ureg = UnitRegistry()
Q_ = ureg.Quantity
app = Flask(__name__)
jobs_executed = []

settings = Dynaconf(envvar_prefix='TIMER',
                    environments=True,
                    load_dotenv=True,
                    dotenv_verbose=True,
                    settings_files=['settings.toml'])

job_defaults = {'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 4}
executors = {'default': ThreadPoolExecutor(1)}

scheduler = BackgroundScheduler()
scheduler.configure(job_defaults=job_defaults,
                    executors=executors,
                    timezone=pytz.timezone('GMT'))


def wemo_on():
    @tenacity.retry(wait=wait_fixed(10) + wait_random(-2, 2),
                    stop=stop_after_delay(60 * 60),
                    before_sleep_log=before_sleep_log(_LOGGER, logging.INFO))
    def discover_and_on():
        address = settings.wemo_address
        port = pywemo.ouimeaux_device.probe_wemo(address)
        url = 'http://%s:%i/setup.xml' % (address, port)
        device = pywemo.discovery.device_from_description(url, None)
        device.on()
        _LOGGER.info("Called on on %s", device)

    discover_and_on()


def wemo_off():
    @tenacity.retry(wait=wait_fixed(10),
                    before_sleep_log=before_sleep_log(_LOGGER, logging.INFO))
    def discover_and_off():
        address = settings.wemo_address
        port = pywemo.ouimeaux_device.probe_wemo(address)
        url = 'http://%s:%i/setup.xml' % (address, port)
        device = pywemo.discovery.device_from_description(url, None)
        device.off()
        _LOGGER.info("Called off on %s", device)

    discover_and_off()


def on_job() -> dict[str, str]:
    """
    This should block until executed successfully.
    """
    _LOGGER.info("Executing on job")
    return "ok"


def off_job() -> dict[str, str]:
    """
    This should block until executed successfully.
    """
    _LOGGER.info("Executing off job")
    return "ok"


def generate_transitions() -> None:
    today = date.today()
    max_time = datetime.combine(today, time.fromisoformat(settings.max_time))
    start_time = datetime.combine(today,
                                  time.fromisoformat(settings.start_time))

    min_off_time = Q_(settings.min_off_time).to('seconds').magnitude
    max_off_time = Q_(settings.max_off_time).to('seconds').magnitude
    min_on_time = Q_(settings.min_on_time).to('seconds').magnitude
    max_on_time = Q_(settings.max_on_time).to('seconds').magnitude

    ret = list()
    ret.append({'off': start_time})  # initial state
    while len(ret) <= settings.max_transitions:
        states = {}
        wait = random.randint(min_off_time, max_off_time)
        states['on'] = ret[-1]['off'] + timedelta(seconds=wait)
        if states['on'] > max_time:
            break
        on = random.randint(min_on_time, max_on_time)
        states['off'] = states['on'] + timedelta(seconds=on)
        _LOGGER.info("Generated transition %s", states)

        ret.append(states)

    if random.random() <= settings.prob_of_execution:
        create_jobs(ret)
    else:
        _LOGGER.info("Skipped")


def create_jobs(transitions: list[dict[str, datetime]]) -> None:
    for transition in transitions:
        _LOGGER.info("Creating transition %s", transition)
        if off := transition.get('off'):
            scheduler.add_job(off_job,
                              trigger=CronTrigger(year=off.year,
                                                  month=off.month,
                                                  day=off.day,
                                                  hour=off.hour,
                                                  minute=off.minute,
                                                  second=off.second),
                              max_instances=1)
        if on := transition.get('on'):
            scheduler.add_job(on_job,
                              trigger=CronTrigger(year=on.year,
                                                  month=on.month,
                                                  day=on.day,
                                                  hour=on.hour,
                                                  minute=on.minute,
                                                  second=on.second),
                              max_instances=1)


def serialise_job(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "name": job.name,
        "next_run_time": job.next_run_time,
        "pending": job.pending
    }


def serialize_event(event: JobExecutionEvent) -> dict[str, str]:
    return {
        "id": event.job_id,
        "run_time": event.scheduled_run_time,
        "alias": event.alias,
        "code": str(event.code),
        "retval": event.retval,
        "exception": str(event.exception)
    }


@app.route("/jobs")
def get_jobs():
    return jsonify([
        serialise_job(job)
        for job in sort_by_next_run_time(scheduler.get_jobs())
    ])


@app.route("/executed")
def list_executed():
    return jsonify([serialize_event(event) for event in jobs_executed])


def sort_by_next_run_time(jobs: list[Job]):
    return sorted(
        jobs,
        key=lambda job: job.next_run_time
        if job.next_run_time else datetime.min.replace(tzinfo=timezone.utc))


@app.route("/pause/<job_id>")
def pause_job(job_id):
    _LOGGER.info("Called pause for %s", job_id)
    job: Job
    job = scheduler.get_job(job_id)
    if not job:
        raise NotFound(f"job_id {job_id} not found")
    job.pause()
    return jsonify(serialise_job(job))


@app.route("/resume/<job_id>")
def resume_job(job_id):
    _LOGGER.info("Called resume for %s", job_id)
    job: Job
    job = scheduler.get_job(job_id)
    if not job:
        raise NotFound(f"job_id {job_id} not found")
    job.resume()
    return jsonify(serialise_job(job))


def executed_job(event: JobExecutionEvent):
    jobs_executed.append(event)
    _LOGGER.info("Job executed: %s", event)


@click.command()
@click.option('-v',
              '--verbosity',
              show_default=True,
              show_choices=True,
              type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                                case_sensitive=True))
def start(verbosity):
    fmt = '%(asctime)s %(name)-12s %(levelname)-8s %(message)s'
    if verbosity:
        logging.basicConfig(level=verbosity, format=fmt)
    else:
        logging.basicConfig(level=settings.log_level, format=fmt)

    _LOGGER.info(settings.to_dict())

    scheduler.add_listener(executed_job, EVENT_JOB_EXECUTED)

    _init_time = time.fromisoformat(settings.init_time)
    scheduler.add_job(generate_transitions,
                      trigger=CronTrigger(hour=_init_time.hour,
                                          minute=_init_time.minute,
                                          second=_init_time.second),
                      max_instances=1)

    scheduler.start()
    app.run(host="localhost", port=8080, debug=False)
