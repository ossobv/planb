from django.contrib import admin
from django.urls import re_path as url
from django.views.generic.base import RedirectView

from .views import EnqueueJob


admin.autodiscover()
admin.site.site_title = 'PlanB'
admin.site.site_header = 'PlanB management'

urlpatterns = [
    #################
    # Admin interface
    #################

    # Use / as the admin path (only if this is the only app in the project)
    # (point people to the right url.. fails to work if STATIC_URL is '/')
    url(r'^admin(/.*)$', RedirectView.as_view(url='/', permanent=False)),
    url(r'^planb/fileset/(?P<fileset_id>\d+)/enqueue/$',
        EnqueueJob.as_view(), name='enqueue'),
    url(r'', admin.site.urls),
]
