import logging
from datetime import date
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from planb.models import HostGroup

logger = logging.getLogger(__name__)


class AbstractPoster(object):
    def post(self, data):
        raise NotImplementedError()

    def handle_response(self, response):
        pass


class HttpPoster(AbstractPoster):
    def __init__(self, url):
        self._url = url
        self._data = None

    def set_data(self, data):
        self._data = data

    def get_method(self):
        return 'POST'

    def get_url(self):
        return self._url

    def get_data_type(self):
        return 'application/x-www-form-urlencoded'

    def get_data(self):
        assert self._data
        return urlencode(self._data).encode('utf-8')

    def get_headers(self):
        return {
            'Content-type': self.get_data_type(),
            'User-Agent': 'PlanB',
            'Auth-Token': 'some-auth-token-here',
        }

    def post(self, data):
        self.set_data(data)

        req = Request(
            method=self.get_method(), url=self.get_url(),
            headers=self.get_headers(), data=self.get_data())
        try:
            fp = urlopen(req)
            resp = fp.read()
            self.handle_response(resp)
        except HTTPError:
            logger.exception('error during POST')


class BossoBillingPoster(HttpPoster):
    def __init__(self, url):
        logger.info('BossoBillingPoster: using url "%s"', url)
        super().__init__(url)

    def post(self, data):
        logger.info('BossoBillingPoster: pushing "%s"', data)

        super().post(data)

    def handle_response(self, response):
        response = response.decode('ascii', 'replace')

        if response == 'OK':
            pass
        elif (('Backup history with this '
               'Name and Date already exists') in response):
            logger.warning(response)
        else:
            logger.error(response)


def daily_hostgroup_report(data_poster):
    """
    This could be run daily to report to REMOTE how many data each
    hostgroup has backed up.
    """
    first_day_of_this_month = date.today()
    today_is_first_day_of_the_month = (first_day_of_this_month.day == 1)
    first_day_of_this_month = first_day_of_this_month.replace(day=1)

    for hostgroup in HostGroup.objects.order_by('name'):
        for hostconfig in (
                # Find hostconfigs which succeeded at least once.
                hostgroup.hostconfigs.exclude(last_ok=None)
                .order_by('friendly_name')):

            # Take date, and drop microseconds and timezone.
            date_ = hostconfig.last_ok.replace(microsecond=0, tzinfo=None)

            if not hostconfig.enabled:
                # Special hacks here. REMOTE will accept duplicate values,
                # but only for the 0th second of the month. If we're pushing
                # old records -- for stale/disabled backups -- we'll update
                # the time to the 0th second of this month. That way we'll
                # get 1 backupinfo record for every month and the hostgroup
                # can get billed for it.
                if not today_is_first_day_of_the_month:
                    continue
                # Update the date to the 0th second of the month.
                date_ = first_day_of_this_month

            # Set values and post.
            data = {
                'name': '{}-{}'.format(
                    hostgroup.name, hostconfig.friendly_name),
                'date': date_,
                'size': hostconfig.total_size_mb << 20  # MiB to B
            }
            data_poster.post(data)
