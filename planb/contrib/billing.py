import logging
from datetime import date
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import requests

from planb.models import HostGroup

logger = logging.getLogger(__name__)


class BasePoster(object):
    def __init__(self, url, use_batch=True):
        logger.info('%s: using url "%s"', self.__class__.__name__, url)
        self.url = url
        self.use_batch = use_batch
        self.data = []

    def add(self, hostgroup, hostconfig, report_date):
        data = self.format_data(hostgroup, hostconfig, report_date)
        if self.use_batch:
            self.data.append(data)
        else:
            self.post(data)

    def close_batch(self):
        if self.use_batch:
            self.post(self.data)
            self.data = []


class BossoBillingPoster(BasePoster):
    content_type = 'application/x-www-form-urlencoded'

    def __init__(self, url, use_batch=False):
        # Does not support batches, disable by default, allow override.
        super().__init__(url, use_batch)

    def get_headers(self):
        return {
            'Content-type': self.content_type,
            'User-Agent': 'PlanB',
        }

    def post(self, data):
        logger.info('%s: pushing "%s"',  self.__class__.__name__, data)
        assert data
        data = urlencode(data).encode('utf-8')

        req = Request(
            method='POST', url=self.url,
            headers=self.get_headers(), data=data)
        try:
            fp = urlopen(req)
            resp = fp.read()
            self.handle_response(resp)
        except HTTPError:
            logger.exception('%s: error during POST', self.__class__.__name__)

    def handle_response(self, response):
        response = response.decode('ascii', 'replace')

        if response == 'OK':
            pass
        elif (('Backup history with this '
               'Name and Date already exists') in response):
            logger.warning(response)
        else:
            logger.error(response)

    def format_data(self, hostgroup, hostconfig, report_date):
        return {
            'name': '{}-{}'.format(
                hostgroup.name, hostconfig.friendly_name),
            'date': report_date,
            'size': hostconfig.total_size_mb << 20  # MiB to B
        }


class BossoRESTPoster(BasePoster):
    UNIQUE_ERROR = [
        'The fields relation_code, item_code, service_code, date must make a '
        'unique set.']

    def __init__(self, url, auth_token, use_batch=True):
        super().__init__(url, use_batch)
        self.auth_token = auth_token
        self.session = requests.Session()
        self.session.headers.update(self.get_headers())

    def get_headers(self):
        return {
            'User-Agent': 'PlanB',
            'Authorization': 'Token {}'.format(self.auth_token),
        }

    def post(self, data):
        logger.info('%s: pushing "%s"',  self.__class__.__name__, data)
        url = '{}create_many/'.format(self.url) if self.use_batch else self.url
        try:
            response = self.session.post(url, json=data)
        except requests.RequestException:
            logger.exception(
                '%s: error during POST', self.__class__.__name__)
            return

        if 200 <= response.status_code < 300:
            pass  # Success.
        elif response.status_code == 400:
            error = response.json()
            # One or more data points were invalid.
            if self.use_batch:
                remaining = []
                for d, e in zip(data, error):
                    if len(e) == 0:
                        remaining.append(d)
                    else:
                        self.handle_error(d, e)
                # Post the items that did not fail validation.
                if remaining:
                    self.post(remaining)
            else:
                self.handle_error(data, error)
        else:
            try:
                response.raise_for_status()
            except requests.RequestException:
                logger.exception(
                    '%s: error during POST', self.__class__.__name__)

    def handle_error(self, data, error):
        if error.get('non_field_errors', []) == self.UNIQUE_ERROR:
            logger.warning('%r returned error %r', data, error)
        else:
            logger.error('%r returned error %r', data, error)

    def format_data(self, hostgroup, hostconfig, report_date):
        return {
            'relation_code': hostgroup.name,
            'item_code': hostconfig.friendly_name,
            'service_code': 'backup-size-gibibyte',
            'date': report_date.strftime('%Y-%m-%d'),
            'value': round(hostconfig.total_size_mb / 1024, 5),  # MiB to GiB
            'unit': 'GiB',
        }


def daily_hostgroup_report(data_poster):
    """
    This could be run daily to report to REMOTE how many data each
    hostgroup has backed up.
    """
    today = date.today()
    today_is_first_day_of_the_month = (today.day == 1)
    first_day_of_this_month = today.replace(day=1)

    for hostgroup in HostGroup.objects.order_by('name'):
        for hostconfig in (
                # Find hostconfigs which succeeded at least once.
                hostgroup.hostconfigs.exclude(last_ok=None)
                .order_by('friendly_name')):

            if not hostconfig.enabled:
                # Always push data for disabled hosts on the first of the month
                # so the hostgroup can get billed for it.
                if not today_is_first_day_of_the_month:
                    continue
                date_ = first_day_of_this_month
            else:
                # Take date, and drop microseconds and timezone.
                date_ = hostconfig.last_ok.replace(microsecond=0, tzinfo=None)
            # Add or post the data.
            data_poster.add(hostgroup, hostconfig, date_)
        # Post the batch if needed.
        data_poster.close_batch()
