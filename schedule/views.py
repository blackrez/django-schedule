from schedule.utils import serialize_occurrences
from urllib import quote
from django.shortcuts import render_to_response, get_object_or_404
from django.views.generic.create_update import delete_object
from django.http import HttpResponseRedirect, Http404, HttpResponse
from django.template import RequestContext
from django.template import Context, loader
from django.core import serializers
from django.core.urlresolvers import reverse
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.views.generic.create_update import delete_object
import datetime

from schedule.conf.settings import GET_EVENTS_FUNC, OCCURRENCE_CANCEL_REDIRECT
from schedule.forms import EventForm, OccurrenceForm
from schedule.forms import EventBackendForm, OccurrenceBackendForm
from schedule.models import *
from schedule.periods import weekday_names, Period
from schedule.utils import check_event_permissions, coerce_date_dict
from schedule.utils import decode_occurrence, serialize_occurrences

def calendar(request, calendar_slug, template='schedule/calendar.html', extra_context=None):
    """
    This view returns a calendar.  This view should be used if you are
    interested in the meta data of a calendar, not if you want to display a
    calendar.  It is suggested that you use calendar_by_periods if you would
    like to display a calendar.

    Context Variables:

    ``calendar``
        The Calendar object designated by the ``calendar_slug``.
    """
    extra_context = extra_context or {}
    calendar = get_object_or_404(Calendar, slug=calendar_slug)
    context = {"calendar": calendar}
    context.update(extra_context)
    return render_to_response(template, context, context_instance=RequestContext(request))

def calendar_by_periods(request, calendar_slug, periods=None,
    template_name="schedule/calendar_by_period.html", extra_context=None):
    """
    This view is for getting a calendar, but also getting periods with that
    calendar.  Which periods you get, is designated with the list periods. You
    can designate which date you the periods to be initialized to by passing
    a date in request.GET. See the template tag ``query_string_for_date``

    Context Variables

    ``date``
        This was the date that was generated from the query string.

    ``periods``
        this is a dictionary that returns the periods from the list you passed
        in.  If you passed in Month and Day, then your dictionary would look
        like this

        {
            'month': <schedule.periods.Month object>
            'day':   <schedule.periods.Day object>
        }

        So in the template to access the Day period in the context you simply
        use ``periods.day``.

    ``calendar``
        This is the Calendar that is designated by the ``calendar_slug``.

    ``weekday_names``
        This is for convenience. It returns the local names of weekedays for
        internationalization.

    """
    extra_context = extra_context or {}
    calendar = get_object_or_404(Calendar, slug=calendar_slug)
    date, end = coerce_date_dict(request.GET)
    event_list = GET_EVENTS_FUNC(request, calendar)
    period_objects = dict([(period.__name__.lower(), period(event_list, date)) for period in periods])
    context = {
            'date': date,
            'periods': period_objects,
            'calendar': calendar,
            'weekday_names': weekday_names,
            'here':quote(request.get_full_path()),
        }
    context.update(extra_context)
    return render_to_response(template_name, context, context_instance=RequestContext(request),)

def event(request, event_id, template_name="schedule/event.html", extra_context=None):
    """
    This view is for showing an event. It is important to remember that an
    event is not an occurrence.  Events define a set of reccurring occurrences.
    If you would like to display an occurrence (a single instance of a
    recurring event) use occurrence.

    Context Variables:

    event
        This is the event designated by the event_id

    back_url
        this is the url that referred to this view.
    """
    extra_context = extra_context or {}
    event = get_object_or_404(Event, id=event_id)
    back_url = request.META.get('HTTP_REFERER', None)
    try:
        cal = event.calendar_set.get()
    except:
        cal = None
    context = {
        "event": event,
        "back_url" : back_url,
    }
    context.update(extra_context)
    return render_to_response(template_name, context, context_instance=RequestContext(request))

def occurrence(request, event_id,
    template_name="schedule/occurrence.html", *args, **kwargs):
    """
    This view is used to display an occurrence.

    Context Variables:

    ``event``
        the event that produces the occurrence

    ``occurrence``
        the occurrence to be displayed

    ``back_url``
        the url from which this request was refered
    """
    extra_context = kwargs.get('extra_context', None) or {}
    event, occurrence = get_occurrence(event_id, *args, **kwargs)
    back_url = request.META.get('HTTP_REFERER', None)
    context = {
        'event': event,
        'occurrence': occurrence,
        'back_url': back_url,
    }
    context.update(extra_context)
    return render_to_response(template_name, context, context_instance=RequestContext(request))


@check_event_permissions
def edit_occurrence(request, event_id,
    template_name="schedule/edit_occurrence.html", *args, **kwargs):
    extra_context = kwargs.get('extra_context', None) or {}
    event, occurrence = get_occurrence(event_id, *args, **kwargs)
    next = kwargs.get('next', None)
    form = OccurrenceForm(data=request.POST or None, instance=occurrence)
    if form.is_valid():
        occurrence = form.save(commit=False)
        occurrence.event = event
        occurrence.save()
        next = next or get_next_url(request, occurrence.get_absolute_url())
        return HttpResponseRedirect(next)
    next = next or get_next_url(request, occurrence.get_absolute_url())
    context = {
        'form': form,
        'occurrence': occurrence,
        'next':next,
    }
    context.update(extra_context)
    return render_to_response(template_name, context, context_instance=RequestContext(request))


@check_event_permissions
def cancel_occurrence(request, event_id,
    template_name='schedule/cancel_occurrence.html', *args, **kwargs):
    """
    This view is used to cancel an occurrence. If it is called with a POST it
    will cancel the view. If it is called with a GET it will ask for
    conformation to cancel.
    """
    extra_context = kwargs.get('extra_context', None) or {}
    event, occurrence = get_occurrence(event_id, *args, **kwargs)
    next = kwargs.get('next', None) or get_next_url(request, event.get_absolute_url())
    if request.method != "POST":
        context = {
            "occurrence": occurrence,
            "next":next,
        }
        context.update(extra_context)
        return render_to_response(template_name, context, context_instance=RequestContext(request))
    occurrence.cancel()
    return HttpResponseRedirect(next)


def get_occurrence(event_id, occurrence_id=None, year=None, month=None,
    day=None, hour=None, minute=None, second=None):
    """
    Because occurrences don't have to be persisted, there must be two ways to
    retrieve them. both need an event, but if its persisted the occurrence can
    be retrieved with an id. If it is not persisted it takes a date to
    retrieve it.  This function returns an event and occurrence regardless of
    which method is used.
    """
    if(occurrence_id):
        occurrence = get_object_or_404(Occurrence, id=occurrence_id)
        event = occurrence.event
    elif not [x for x in (year, month, day, hour, minute, second) if x is None]:
        event = get_object_or_404(Event, id=event_id)
        occurrence = event.get_occurrence(
            datetime.datetime(int(year), int(month), int(day), int(hour),
                int(minute), int(second)))
        if occurrence is None:
            raise Http404
    else:
        raise Http404
    return event, occurrence


@check_event_permissions
def create_or_edit_event(request,
                         calendar_slug=None,
                         event_id=None,
                         next=None,
                         template_name='schedule/create_event.html',
                         form_class=EventForm,
                         coerce_date_func=coerce_date_dict,
                         extra_context=None):
    """
    This function, if it receives a GET request or if given an invalid form in a
    POST request it will generate the following response

    Template:
        schedule/create_event.html

    Context Variables:

    form:
        an instance of EventForm

    calendar:
        a Calendar with id=calendar_id

    if this function gets a GET request with ``year``, ``month``, ``day``,
    ``hour``, ``minute``, and ``second`` it will auto fill the form, with
    the date specifed in the GET being the start and 30 minutes from that
    being the end.

    If this form receives an event_id it will edit the event with that id, if it
    recieves a calendar_id and it is creating a new event it will add that event
    to the calendar with the id calendar_id

    If it is given a valid form in a POST request it will redirect with one of
    three options, in this order

    # Try to find a 'next' GET variable
    # If the key word argument redirect is set
    # Lastly redirect to the event detail of the recently create event
    """
    extra_context = extra_context or {}
    start, end = coerce_date_func(request.GET)
    initial_data = {"start": start}
    if end :
        initial_data["end"] = end
    else :
        initial_data["end"] = start + datetime.timedelta(minutes=30)

    instance = None
    if event_id is not None:
        instance = get_object_or_404(Event, id=event_id)

    if calendar_slug:
        calendar = get_object_or_404(Calendar, slug=calendar_slug)

    form = form_class(data=request.POST or None, instance=instance, hour24=True, initial=initial_data)

    if form.is_valid():
        event = form.save(commit=False)
        if instance is None:
            event.creator = request.user
            if calendar_slug:
                event.calendar = calendar
            #else the calendar was specified in the form
        event.save()
        next = next or reverse('event', args=[event.id])
        next = get_next_url(request, next)
        return HttpResponseRedirect(next)

    next = get_next_url(request, next)
    context = {
        "form": form,
        "next":next
    }
    if calendar_slug:
        context["calendar"] = calendar
    context.update(extra_context)
    return render_to_response(template_name, context, context_instance=RequestContext(request))


@check_event_permissions
def delete_event(request, event_id, next=None, login_required=True, extra_context=None):
    """
    After the event is deleted there are three options for redirect, tried in
    this order:

    # Try to find a 'next' GET variable
    # If the key word argument redirect is set
    # Lastly redirect to the event detail of the recently create event
    """
    extra_context = extra_context or {}
    event = get_object_or_404(Event, id=event_id)
    next = next or reverse('day_calendar', args=[event.calendar.slug])
    next = get_next_url(request, next)
    extra_context['next'] = next
    return delete_object(request,
                         model=Event,
                         object_id=event_id,
                         post_delete_redirect=next,
                         template_name="schedule/delete_event.html",
                         extra_context=extra_context,
                         login_required=login_required
                        )

def check_next_url(next):
    """
    Checks to make sure the next url is not redirecting to another page.
    Basically it is a minimal security check.
    """
    if not next or '://' in next:
        return None
    return next

def get_next_url(request, default):
    next = default
    if OCCURRENCE_CANCEL_REDIRECT:
        next = OCCURRENCE_CANCEL_REDIRECT
    if 'next' in request.REQUEST and check_next_url(request.REQUEST['next']) is not None:
        next = request.REQUEST['next']
    return next


class JSONError(HttpResponse):

    def __init__(self, error):
        s = "{error:'%s'}" % error
        HttpResponse.__init__(self, s)
        # TODO strip html tags from form errors

def calendar_by_periods_json(request,
                             calendar_slug,
                             periods,
                             nb_periods=1,
                             get_events_func=GET_EVENTS_FUNC,
                             coerce_date_func=coerce_date_dict,
                             serialize_occurrences_func=serialize_occurrences):
    # XXX is this function name good?
    # it conforms with the standard API structure but in this case it is rather cryptic
    user = request.user
    calendar = get_object_or_404(Calendar, slug=calendar_slug)
    start, end = coerce_date_func(request.GET)
    print start, end

    event_list = get_events_func(request, calendar)
    if not end :
        period_object = periods[0](event_list, start)
    else:
        period_object = Period(event_list, start, end)

    occurrences = []
    for idx in range(nb_periods):
        for o in period_object.occurrences:
            if period_object.classify_occurrence(o):
                occurrences.append(o)
        if hasattr(period_object, "next"):
            period_object = period_object.next()

    resp = serialize_occurrences_func(occurrences, user)
    return HttpResponse(resp)


# TODO permissions check
def ajax_edit_occurrence_by_code(request):
    try:
        id = request.REQUEST.get('id')
        kwargs = decode_occurrence(id)
        event_id = kwargs.pop('event_id')
        event, occurrence = get_occurrence(event_id, **kwargs)
        if request.REQUEST.get('action') == 'cancel':
            occurrence.cancel()
            return HttpResponse(serialize_occurrences([occurrence], request.user))
        form = OccurrenceBackendForm(data=request.POST or None, instance=occurrence)
        if form.is_valid():
            occurrence = form.save(commit=False)
            occurrence.event = event
            occurrence.save()
            return HttpResponse(serialize_occurrences([occurrence], request.user))
        return JSONError(form.errors)
    except Exception, e:
        import traceback
        traceback.print_exc()
        return JSONError(e)


#TODO permission control
def ajax_edit_event(request, calendar_slug):
    try:
        id = request.REQUEST.get('id') # we got occurrence's encoded id or event id
        if id:
            kwargs = decode_occurrence(id)
            if kwargs:
                event_id = kwargs['event_id']
            else:
                event_id = id
            event = Event.objects.get(pk=event_id)
            # deleting an event
            if request.REQUEST.get('action') == 'cancel':
                # cancellation of a non-recurring event means deleting the event
                event.delete()
                # there is nothing more - we return empty json
                return HttpResponse(serialize_occurrences([], request.user))
            else:
                form = EventBackendForm(data=request.POST, instance=event)
                if form.is_valid():
                    event = form.save()
                    return HttpResponse(serialize_occurrences(event.get_occurrences(event.start, event.end), request.user))
                return JSONError(form.errors)
        else:
            calendar = get_object_or_404(Calendar, slug=calendar_slug)
            # creation of an event
            form = EventBackendForm(data=request.POST)
            if form.is_valid():
                event = form.save(commit=False)
                event.creator = request.user
                event.calendar = calendar
                event.save()
                return HttpResponse(serialize_occurrences(event.get_occurrences(event.start, event.end), request.user))
            return JSONError(form.errors)
    except Exception, e:
        import traceback
        traceback.print_exc()
        return JSONError(e)


#TODO permission control
def event_json(request):
    event_id = request.REQUEST.get('event_id')
    event = get_object_or_404(Event, pk=event_id)
    event.rule_id = event.rule_id or "false"
    rnd = loader.get_template('schedule/event_json.html')
    resp = rnd.render(Context({'event':event}))
    return HttpResponse(resp)
