# M13 review sheet

Label each answer with the rubric. Judge only from the cited text shown; knowledge of the repository outside it does not count. Do not ask whether the answer is good.

## flask-050 (set A)

**Question:** What uses `_default_template_ctx_processor`?

**Status:** insufficient_evidence   **Missing:** The provided evidence does not show any usages or references to `_default_template_ctx_processor` beyond its definition and import.

#### Everything the model was given (for rubric item 5)

*E1: src/flask/templating.py lines 21-33 (_default_template_ctx_processor)*
```python
def _default_template_ctx_processor() -> dict[str, t.Any]:
    """Default template context processor.  Replaces the ``request`` and ``g``
    proxies with their concrete objects for faster access.
    """
    ctx = app_ctx._get_current_object()
    rv: dict[str, t.Any] = {"g": ctx.g}

    if ctx.has_request:
        rv["request"] = ctx.request
        # The session proxy cannot be replaced, accessing it gets
        # RequestContext.session, which sets session.accessed.

    return rv
```

*E2: src/flask/sansio/scaffold.py lines 1-39 (module-level)*
```python
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import typing as t
from collections import defaultdict
from functools import update_wrapper

from jinja2 import BaseLoader
from jinja2 import FileSystemLoader
from werkzeug.exceptions import default_exceptions
from werkzeug.exceptions import HTTPException
from werkzeug.utils import cached_property

from .. import typing as ft
from ..helpers import get_root_path
from ..templating import _default_template_ctx_processor

if t.TYPE_CHECKING:  # pragma: no cover
    from click import Group

# a singleton sentinel value for parameter defaults
_sentinel = object()

F = t.TypeVar("F", bound=t.Callable[..., t.Any])
T_after_request = t.TypeVar("T_after_request", bound=ft.AfterRequestCallable[t.Any])
T_before_request = t.TypeVar("T_before_request", bound=ft.BeforeRequestCallable)
T_error_handler = t.TypeVar("T_error_handler", bound=ft.ErrorHandlerCallable)
T_teardown = t.TypeVar("T_teardown", bound=ft.TeardownCallable)
T_template_context_processor = t.TypeVar(
    "T_template_context_processor", bound=ft.TemplateContextProcessorCallable
)
T_url_defaults = t.TypeVar("T_url_defaults", bound=ft.URLDefaultCallable)
T_url_value_preprocessor = t.TypeVar(
    "T_url_value_preprocessor", bound=ft.URLValuePreprocessorCallable
)
T_route = t.TypeVar("T_route", bound=ft.RouteCallable)
```

*E3: src/flask/sansio/blueprints.py lines 643-653 (Blueprint.app_context_processor)*
```python
    @setupmethod
    def app_context_processor(
        self, f: T_template_context_processor
    ) -> T_template_context_processor:
        """Like :meth:`context_processor`, but for templates rendered by every view, not
        only by the blueprint. Equivalent to :meth:`.Flask.context_processor`.
        """
        self.record_once(
            lambda s: s.app.template_context_processors.setdefault(None, []).append(f)
        )
        return f
```

*E4: tests/test_templating.py lines 42-50 (test_request_less_rendering)*
```python
def test_request_less_rendering(app, app_ctx):
    app.config["WORLD_NAME"] = "Special World"

    @app.context_processor
    def context_processor():
        return dict(foo=42)

    rv = flask.render_template_string("Hello {{ config.WORLD_NAME }} {{ foo }}")
    assert rv == "Hello Special World 42"
```

*E5: src/flask/app.py lines 591-619 (Flask.update_template_context)*
```python
    def update_template_context(
        self, ctx: AppContext, context: dict[str, t.Any]
    ) -> None:
        """Update the template context with some commonly used variables.
        This injects request, session, config and g into the template
        context as well as everything template context processors want
        to inject.  Note that the as of Flask 0.6, the original values
        in the context will not be overridden if a context processor
        decides to return a value with the same key.

        :param context: the context as a dictionary that is updated in place
                        to add extra variables.
        """
        names: t.Iterable[str | None] = (None,)

        # A template may be rendered outside a request context.
        if ctx.has_request:
            names = chain(names, reversed(ctx.request.blueprints))

        # The values passed to render_template take precedence. Keep a
        # copy to re-apply after all context functions.
        orig_ctx = context.copy()

        for name in names:
            if name in self.template_context_processors:
                for func in self.template_context_processors[name]:
                    context.update(self.ensure_sync(func)())

        context.update(orig_ctx)
```

*E6: src/flask/sansio/app.py lines 1-49 (module-level)*
```python
from __future__ import annotations

import logging
import os
import sys
import typing as t
from datetime import timedelta
from itertools import chain

from werkzeug.exceptions import Aborter
from werkzeug.exceptions import BadRequest
from werkzeug.exceptions import BadRequestKeyError
from werkzeug.routing import BuildError
from werkzeug.routing import Map
from werkzeug.routing import Rule
from werkzeug.sansio.response import Response
from werkzeug.utils import cached_property
from werkzeug.utils import redirect as _wz_redirect

from .. import typing as ft
from ..config import Config
from ..config import ConfigAttribute
from ..ctx import _AppCtxGlobals
from ..helpers import _split_blueprint_path
from ..helpers import get_debug_flag
from ..json.provider import DefaultJSONProvider
from ..json.provider import JSONProvider
from ..logging import create_logger
from ..templating import DispatchingJinjaLoader
from ..templating import Environment
from .scaffold import _endpoint_from_view_func
from .scaffold import find_package
from .scaffold import Scaffold
from .scaffold import setupmethod

if t.TYPE_CHECKING:  # pragma: no cover
    from werkzeug.wrappers import Response as BaseResponse

    from ..testing import FlaskClient
    from ..testing import FlaskCliRunner
    from .blueprints import Blueprint

T_shell_context_processor = t.TypeVar(
    "T_shell_context_processor", bound=ft.ShellContextProcessorCallable
)
T_teardown = t.TypeVar("T_teardown", bound=ft.TeardownCallable)
T_template_filter = t.TypeVar("T_template_filter", bound=ft.TemplateFilterCallable)
T_template_global = t.TypeVar("T_template_global", bound=ft.TemplateGlobalCallable)
T_template_test = t.TypeVar("T_template_test", bound=ft.TemplateTestCallable)
```

*E7: tests/test_blueprints.py lines 670-708 (test_context_processing)*
```python
def test_context_processing(app, client):
    answer_bp = flask.Blueprint("answer_bp", __name__)

    def template_string():
        return flask.render_template_string(
            "{% if notanswer %}{{ notanswer }} is not the answer. {% endif %}"
            "{% if answer %}{{ answer }} is the answer.{% endif %}"
        )

    # App global context processor
    @answer_bp.app_context_processor
    def not_answer_context_processor():
        return {"notanswer": 43}

    # Blueprint local context processor
    @answer_bp.context_processor
    def answer_context_processor():
        return {"answer": 42}

    # Setup endpoints for testing
    @answer_bp.route("/bp")
    def bp_page():
        return template_string()

    @app.route("/")
    def app_page():
        return template_string()

    # Register the blueprint
    app.register_blueprint(answer_bp)

    app_page_bytes = client.get("/").data
    answer_page_bytes = client.get("/bp").data

    assert b"43" in app_page_bytes
    assert b"42" not in app_page_bytes

    assert b"42" in answer_page_bytes
    assert b"43" in answer_page_bytes
```

*E8: src/flask/cli.py lines 1122-1123 (main)*
```python
def main() -> None:
    cli.main()
```

**Rubric** (PASS / FAIL / UNCERTAIN for each; write a short reason for FAIL/UNCERTAIN):

| # | Question | Label | Reason |
|---|---|---|---|
| 1 | Is every factual claim supported by the cited evidence? | | |
| 2 | Does every citation actually support the claim it is attached to? | | |
| 3 | Are the cited line ranges correct? | | |
| 4 | Are there factual claims without a citation? | | |
| 5 | (abstentions only) Did it abstain correctly, `missing` naming a real gap? | | |

---
## flask-054 (set A)

**Question:** Which files import `flask.json.provider`?

**Status:** answered   **Missing:** -

### Claim 1: tests/test_json.py imports flask.json.provider
Cites: E1

*E1: tests/test_json.py lines 1-11 (module-level)*
```python
import datetime
import decimal
import io
import uuid

import pytest
from werkzeug.http import http_date

import flask
from flask import json
from flask.json.provider import DefaultJSONProvider
```

### Claim 2: src/flask/json/__init__.py imports .provider
Cites: E4

*E4: src/flask/json/__init__.py lines 1-10 (module-level)*
```python
from __future__ import annotations

import json as _json
import typing as t

from ..globals import current_app
from .provider import _default

if t.TYPE_CHECKING:  # pragma: no cover
    from ..wrappers import Response
```

**Rubric** (PASS / FAIL / UNCERTAIN for each; write a short reason for FAIL/UNCERTAIN):

| # | Question | Label | Reason |
|---|---|---|---|
| 1 | Is every factual claim supported by the cited evidence? | | |
| 2 | Does every citation actually support the claim it is attached to? | | |
| 3 | Are the cited line ranges correct? | | |
| 4 | Are there factual claims without a citation? | | |
| 5 | (abstentions only) Did it abstain correctly, `missing` naming a real gap? | | |

---
## flask-006 (set A)

**Question:** Where is `AppContext` defined?

**Status:** answered   **Missing:** -

### Claim 1: `AppContext` is defined in `src/flask/ctx.py`.
Cites: E3

*E3: src/flask/ctx.py lines 260-298 (AppContext)*
```python
class AppContext:
    """An app context contains information about an app, and about the request
    when handling a request. A context is pushed at the beginning of each
    request and CLI command, and popped at the end. The context is referred to
    as a "request context" if it has request information, and an "app context"
    if not.

    Do not use this class directly. Use :meth:`.Flask.app_context` to create an
    app context if needed during setup, and :meth:`.Flask.test_request_context`
    to create a request context if needed during tests.

    When the context is popped, it will evaluate all the teardown functions
    registered with :meth:`~flask.Flask.teardown_request` (if handling a
    request) then :meth:`.Flask.teardown_appcontext`.

    When using the interactive debugger, the context will be restored so
    ``request`` is still accessible. Similarly, the test client can preserve the
    context after the request ends. However, teardown functions may already have
    closed some resources such as database connections, and will run again when
    the restored context is popped.

    :param app: The application this context represents.
    :param request: The request data this context represents.
    :param session: The session data this context represents. If not given,
        loaded from the request on first access.

    .. versionchanged:: 3.2
        Merged with ``RequestContext``. The ``RequestContext`` alias will be
        removed in Flask 4.0.

    .. versionchanged:: 3.2
        A combined app and request context is pushed for every request and CLI
        command, rather than trying to detect if an app context is already
        pushed.

    .. versionchanged:: 3.2
        The session is loaded the first time it is accessed, rather than when
        the context is pushed.
    """
```

**Rubric** (PASS / FAIL / UNCERTAIN for each; write a short reason for FAIL/UNCERTAIN):

| # | Question | Label | Reason |
|---|---|---|---|
| 1 | Is every factual claim supported by the cited evidence? | | |
| 2 | Does every citation actually support the claim it is attached to? | | |
| 3 | Are the cited line ranges correct? | | |
| 4 | Are there factual claims without a citation? | | |
| 5 | (abstentions only) Did it abstain correctly, `missing` naming a real gap? | | |

---
## flask-034 (set A)

**Question:** Where is the debug flag read from the environment?

**Status:** answered   **Missing:** -

### Claim 1: The debug flag is read from the FLASK_DEBUG environment variable.
Cites: E1

*E1: src/flask/helpers.py lines 28-33 (get_debug_flag)*
```python
def get_debug_flag() -> bool:
    """Get whether debug mode should be enabled for the app, indicated by the
    :envvar:`FLASK_DEBUG` environment variable. The default is ``False``.
    """
    val = os.environ.get("FLASK_DEBUG")
    return bool(val and val.lower() not in {"0", "false", "no"})
```

**Rubric** (PASS / FAIL / UNCERTAIN for each; write a short reason for FAIL/UNCERTAIN):

| # | Question | Label | Reason |
|---|---|---|---|
| 1 | Is every factual claim supported by the cited evidence? | | |
| 2 | Does every citation actually support the claim it is attached to? | | |
| 3 | Are the cited line ranges correct? | | |
| 4 | Are there factual claims without a citation? | | |
| 5 | (abstentions only) Did it abstain correctly, `missing` naming a real gap? | | |

---
## flask-068 (set A)

**Question:** How are before_request and after_request callbacks executed?

**Status:** partial   **Missing:** The evidence does not contain the detailed implementation code showing the exact loop or order of execution for after_request callbacks, though tests show their ordering relative to handlers and signals.

### Claim 1: A function registered with before_request will be called without any arguments, and if it returns a non-None value, that value is handled as if it were the return value from the view, stopping further request handling.
Cites: E3

*E3: src/flask/sansio/scaffold.py lines 467-492 (Scaffold.before_request)*
```python
    @setupmethod
    def before_request(self, f: T_before_request) -> T_before_request:
        """Register a function to run before each request.

        For example, this can be used to open a database connection, or
        to load the logged in user from the session.

        .. code-block:: python

            @app.before_request
            def load_user():
                if "user_id" in session:
                    g.user = db.session.get(session["user_id"])

        The function will be called without any arguments. If it returns
        a non-``None`` value, the value is handled as if it was the
        return value from the view, and further request handling is
        stopped.

        This is available on both app and blueprint objects. When used on an app, this
        executes before every request. When used on a blueprint, this executes before
        every request that the blueprint handles. To register with a blueprint and
        execute before every request, use :meth:`.Blueprint.before_app_request`.
        """
        self.before_request_funcs.setdefault(None, []).append(f)
        return f
```

### Claim 2: When used on an app, before_request executes before every request, and when used on a blueprint, it executes before every request that the blueprint handles.
Cites: E3

*E3: src/flask/sansio/scaffold.py lines 467-492 (Scaffold.before_request)*
```python
    @setupmethod
    def before_request(self, f: T_before_request) -> T_before_request:
        """Register a function to run before each request.

        For example, this can be used to open a database connection, or
        to load the logged in user from the session.

        .. code-block:: python

            @app.before_request
            def load_user():
                if "user_id" in session:
                    g.user = db.session.get(session["user_id"])

        The function will be called without any arguments. If it returns
        a non-``None`` value, the value is handled as if it was the
        return value from the view, and further request handling is
        stopped.

        This is available on both app and blueprint objects. When used on an app, this
        executes before every request. When used on a blueprint, this executes before
        every request that the blueprint handles. To register with a blueprint and
        execute before every request, use :meth:`.Blueprint.before_app_request`.
        """
        self.before_request_funcs.setdefault(None, []).append(f)
        return f
```

#### Everything the model was given (for rubric item 5)

*E1: tests/test_basic.py lines 710-732 (test_request_processing)*
```python
def test_request_processing(app, client):
    evts = []

    @app.before_request
    def before_request():
        evts.append("before")

    @app.after_request
    def after_request(response):
        response.data += b"|after"
        evts.append("after")
        return response

    @app.route("/")
    def index():
        assert "before" in evts
        assert "after" not in evts
        return "request"

    assert "after" not in evts
    rv = client.get("/").data
    assert "after" in evts
    assert rv == b"request|after"
```

*E2: tests/test_async.py lines 97-145 (test_async_before_after_request)*
```python
def test_async_before_after_request():
    app_before_called = False
    app_after_called = False
    bp_before_called = False
    bp_after_called = False

    app = Flask(__name__)

    @app.route("/")
    def index():
        return ""

    @app.before_request
    async def before():
        nonlocal app_before_called
        app_before_called = True

    @app.after_request
    async def after(response):
        nonlocal app_after_called
        app_after_called = True
        return response

    blueprint = Blueprint("bp", __name__)

    @blueprint.route("/")
    def bp_index():
        return ""

    @blueprint.before_request
    async def bp_before():
        nonlocal bp_before_called
        bp_before_called = True

    @blueprint.after_request
    async def bp_after(response):
        nonlocal bp_after_called
        bp_after_called = True
        return response

    app.register_blueprint(blueprint, url_prefix="/bp")

    test_client = app.test_client()
    test_client.get("/")
    assert app_before_called
    assert app_after_called
    test_client.get("/bp/")
    assert bp_before_called
    assert bp_after_called
```

*E3: src/flask/sansio/scaffold.py lines 467-492 (Scaffold.before_request)*
```python
    @setupmethod
    def before_request(self, f: T_before_request) -> T_before_request:
        """Register a function to run before each request.

        For example, this can be used to open a database connection, or
        to load the logged in user from the session.

        .. code-block:: python

            @app.before_request
            def load_user():
                if "user_id" in session:
                    g.user = db.session.get(session["user_id"])

        The function will be called without any arguments. If it returns
        a non-``None`` value, the value is handled as if it was the
        return value from the view, and further request handling is
        stopped.

        This is available on both app and blueprint objects. When used on an app, this
        executes before every request. When used on a blueprint, this executes before
        every request that the blueprint handles. To register with a blueprint and
        execute before every request, use :meth:`.Blueprint.before_app_request`.
        """
        self.before_request_funcs.setdefault(None, []).append(f)
        return f
```

*E4: src/flask/ctx.py lines 118-148 (after_this_request)*
```python
def after_this_request(
    f: ft.AfterRequestCallable[t.Any],
) -> ft.AfterRequestCallable[t.Any]:
    """Decorate a function to run after the current request. The behavior is the
    same as :meth:`.Flask.after_request`, except it only applies to the current
    request, rather than every request. Therefore, it must be used within a
    request context, rather than during setup.

    .. code-block:: python

        @app.route("/")
        def index():
            @after_this_request
            def add_header(response):
                response.headers["X-Foo"] = "Parachute"
                return response

            return "Hello, World!"

    .. versionadded:: 0.9
    """
    ctx = _cv_app.get(None)

    if ctx is None or not ctx.has_request:
        raise RuntimeError(
            "'after_this_request' can only be used when a request"
            " context is active, such as in a view function."
        )

    ctx._after_request_functions.append(f)
    return f
```

*E5: tests/test_blueprints.py lines 765-793 (test_request_processing)*
```python
def test_request_processing(app, client):
    bp = flask.Blueprint("bp", __name__)
    evts = []

    @bp.before_request
    def before_bp():
        evts.append("before")

    @bp.after_request
    def after_bp(response):
        response.data += b"|after"
        evts.append("after")
        return response

    @bp.teardown_request
    def teardown_bp(exc):
        evts.append("teardown")

    # Setup routes for testing
    @bp.route("/bp")
    def bp_endpoint():
        return "request"

    app.register_blueprint(bp)

    assert evts == []
    rv = client.get("/bp")
    assert rv.data == b"request|after"
    assert evts == ["before", "after", "teardown"]
```

*E6: tests/test_signals.py lines 50-92 (test_request_signals)*
```python
def test_request_signals():
    app = flask.Flask(__name__)
    calls = []

    def before_request_signal(sender):
        calls.append("before-signal")

    def after_request_signal(sender, response):
        assert response.data == b"stuff"
        calls.append("after-signal")

    @app.before_request
    def before_request_handler():
        calls.append("before-handler")

    @app.after_request
    def after_request_handler(response):
        calls.append("after-handler")
        response.data = "stuff"
        return response

    @app.route("/")
    def index():
        calls.append("handler")
        return "ignored anyway"

    flask.request_started.connect(before_request_signal, app)
    flask.request_finished.connect(after_request_signal, app)

    try:
        rv = app.test_client().get("/")
        assert rv.data == b"stuff"

        assert calls == [
            "before-signal",
            "before-handler",
            "handler",
            "after-handler",
            "after-signal",
        ]
    finally:
        flask.request_started.disconnect(before_request_signal, app)
        flask.request_finished.disconnect(after_request_signal, app)
```

*E7: tests/type_check/typing_app_decorators.py lines 23-24 (before_async)*
```python
@app.before_request
async def before_async() -> None: ...
```

*E8: src/flask/sansio/blueprints.py lines 623-631 (Blueprint.after_app_request)*
```python
    @setupmethod
    def after_app_request(self, f: T_after_request) -> T_after_request:
        """Like :meth:`after_request`, but after every request, not only those handled
        by the blueprint. Equivalent to :meth:`.Flask.after_request`.
        """
        self.record_once(
            lambda s: s.app.after_request_funcs.setdefault(None, []).append(f)
        )
        return f
```

**Rubric** (PASS / FAIL / UNCERTAIN for each; write a short reason for FAIL/UNCERTAIN):

| # | Question | Label | Reason |
|---|---|---|---|
| 1 | Is every factual claim supported by the cited evidence? | | |
| 2 | Does every citation actually support the claim it is attached to? | | |
| 3 | Are the cited line ranges correct? | | |
| 4 | Are there factual claims without a citation? | | |
| 5 | (abstentions only) Did it abstain correctly, `missing` naming a real gap? | | |

---
## flask-065 (set A)

**Question:** How does the `@app.route` decorator register a URL rule?

**Status:** answered   **Missing:** -

### Claim 1: The `@app.route` decorator decorates a view function and calls `self.add_url_rule` with the rule, endpoint, view function, and extra options.
Cites: E1

*E1: src/flask/sansio/scaffold.py lines 343-373 (Scaffold.route)*
```python
    @setupmethod
    def route(self, rule: str, **options: t.Any) -> t.Callable[[T_route], T_route]:
        """Decorate a view function to register it with the given URL
        rule and options. Calls :meth:`add_url_rule`, which has more
        details about the implementation.

        .. code-block:: python

            @app.route("/")
            def index():
                return "Hello, World!"

        See :ref:`url-route-registrations`.

        The endpoint name for the route defaults to the name of the view
        function if the ``endpoint`` parameter isn't passed.

        The ``methods`` parameter defaults to ``["GET"]``. ``HEAD`` and
        ``OPTIONS`` are added automatically.

        :param rule: The URL rule string.
        :param options: Extra options passed to the
            :class:`~werkzeug.routing.Rule` object.
        """

        def decorator(f: T_route) -> T_route:
            endpoint = options.pop("endpoint", None)
            self.add_url_rule(rule, endpoint, f, **options)
            return f

        return decorator
```

**Rubric** (PASS / FAIL / UNCERTAIN for each; write a short reason for FAIL/UNCERTAIN):

| # | Question | Label | Reason |
|---|---|---|---|
| 1 | Is every factual claim supported by the cited evidence? | | |
| 2 | Does every citation actually support the claim it is attached to? | | |
| 3 | Are the cited line ranges correct? | | |
| 4 | Are there factual claims without a citation? | | |
| 5 | (abstentions only) Did it abstain correctly, `missing` naming a real gap? | | |

---
## flask-052 (set A)

**Question:** Which files import `flask.signals`?

**Status:** partial   **Missing:** The evidence shows structure relationships indicating that src/flask/app.py and src/flask/helpers.py also import src/flask/signals.py, but their file contents are not provided in the evidence to show the exact import statements.

### Claim 1: src/flask/__init__.py imports from .signals
Cites: E1, E2

*E1: src/flask/signals.py lines 1-17 (module-level)*
```python
from __future__ import annotations

from blinker import Namespace

# This namespace is only for signals provided by Flask itself.
_signals = Namespace()

template_rendered = _signals.signal("template-rendered")
before_render_template = _signals.signal("before-render-template")
request_started = _signals.signal("request-started")
request_finished = _signals.signal("request-finished")
request_tearing_down = _signals.signal("request-tearing-down")
got_request_exception = _signals.signal("got-request-exception")
appcontext_tearing_down = _signals.signal("appcontext-tearing-down")
appcontext_pushed = _signals.signal("appcontext-pushed")
appcontext_popped = _signals.signal("appcontext-popped")
message_flashed = _signals.signal("message-flashed")
```

*E2: src/flask/__init__.py lines 1-39 (module-level)*
```python
from . import json as json
from .app import Flask as Flask
from .blueprints import Blueprint as Blueprint
from .config import Config as Config
from .ctx import after_this_request as after_this_request
from .ctx import copy_current_request_context as copy_current_request_context
from .ctx import has_app_context as has_app_context
from .ctx import has_request_context as has_request_context
from .globals import current_app as current_app
from .globals import g as g
from .globals import request as request
from .globals import session as session
from .helpers import abort as abort
from .helpers import flash as flash
from .helpers import get_flashed_messages as get_flashed_messages
from .helpers import get_template_attribute as get_template_attribute
from .helpers import make_response as make_response
from .helpers import redirect as redirect
from .helpers import send_file as send_file
from .helpers import send_from_directory as send_from_directory
from .helpers import stream_with_context as stream_with_context
from .helpers import url_for as url_for
from .json import jsonify as jsonify
from .signals import appcontext_popped as appcontext_popped
from .signals import appcontext_pushed as appcontext_pushed
from .signals import appcontext_tearing_down as appcontext_tearing_down
from .signals import before_render_template as before_render_template
from .signals import got_request_exception as got_request_exception
from .signals import message_flashed as message_flashed
from .signals import request_finished as request_finished
from .signals import request_started as request_started
from .signals import request_tearing_down as request_tearing_down
from .signals import template_rendered as template_rendered
from .templating import render_template as render_template
from .templating import render_template_string as render_template_string
from .templating import stream_template as stream_template
from .templating import stream_template_string as stream_template_string
from .wrappers import Request as Request
from .wrappers import Response as Response
```

### Claim 2: src/flask/templating.py imports from .signals
Cites: E1, E4

*E1: src/flask/signals.py lines 1-17 (module-level)*
```python
from __future__ import annotations

from blinker import Namespace

# This namespace is only for signals provided by Flask itself.
_signals = Namespace()

template_rendered = _signals.signal("template-rendered")
before_render_template = _signals.signal("before-render-template")
request_started = _signals.signal("request-started")
request_finished = _signals.signal("request-finished")
request_tearing_down = _signals.signal("request-tearing-down")
got_request_exception = _signals.signal("got-request-exception")
appcontext_tearing_down = _signals.signal("appcontext-tearing-down")
appcontext_pushed = _signals.signal("appcontext-pushed")
appcontext_popped = _signals.signal("appcontext-popped")
message_flashed = _signals.signal("message-flashed")
```

*E4: src/flask/templating.py lines 1-18 (module-level)*
```python
from __future__ import annotations

import typing as t

from jinja2 import BaseLoader
from jinja2 import Environment as BaseEnvironment
from jinja2 import Template
from jinja2 import TemplateNotFound

from .ctx import AppContext
from .globals import app_ctx
from .helpers import stream_with_context
from .signals import before_render_template
from .signals import template_rendered

if t.TYPE_CHECKING:  # pragma: no cover
    from .sansio.app import App
    from .sansio.scaffold import Scaffold
```

### Claim 3: src/flask/ctx.py imports from .signals
Cites: E1, E7

*E1: src/flask/signals.py lines 1-17 (module-level)*
```python
from __future__ import annotations

from blinker import Namespace

# This namespace is only for signals provided by Flask itself.
_signals = Namespace()

template_rendered = _signals.signal("template-rendered")
before_render_template = _signals.signal("before-render-template")
request_started = _signals.signal("request-started")
request_finished = _signals.signal("request-finished")
request_tearing_down = _signals.signal("request-tearing-down")
got_request_exception = _signals.signal("got-request-exception")
appcontext_tearing_down = _signals.signal("appcontext-tearing-down")
appcontext_pushed = _signals.signal("appcontext-pushed")
appcontext_popped = _signals.signal("appcontext-popped")
message_flashed = _signals.signal("message-flashed")
```

*E7: src/flask/ctx.py lines 1-27 (module-level)*
```python
from __future__ import annotations

import contextvars
import typing as t
from functools import update_wrapper
from types import TracebackType

from werkzeug.exceptions import HTTPException
from werkzeug.routing import MapAdapter

from . import typing as ft
from .globals import _cv_app
from .helpers import _CollectErrors
from .signals import appcontext_popped
from .signals import appcontext_pushed

if t.TYPE_CHECKING:
    import typing_extensions as te
    from _typeshed.wsgi import WSGIEnvironment

    from .app import Flask
    from .sessions import SessionMixin
    from .wrappers import Request


# a singleton sentinel value for parameter defaults
_sentinel = object()
```

#### Everything the model was given (for rubric item 5)

*E1: src/flask/signals.py lines 1-17 (module-level)*
```python
from __future__ import annotations

from blinker import Namespace

# This namespace is only for signals provided by Flask itself.
_signals = Namespace()

template_rendered = _signals.signal("template-rendered")
before_render_template = _signals.signal("before-render-template")
request_started = _signals.signal("request-started")
request_finished = _signals.signal("request-finished")
request_tearing_down = _signals.signal("request-tearing-down")
got_request_exception = _signals.signal("got-request-exception")
appcontext_tearing_down = _signals.signal("appcontext-tearing-down")
appcontext_pushed = _signals.signal("appcontext-pushed")
appcontext_popped = _signals.signal("appcontext-popped")
message_flashed = _signals.signal("message-flashed")
```

*E2: src/flask/__init__.py lines 1-39 (module-level)*
```python
from . import json as json
from .app import Flask as Flask
from .blueprints import Blueprint as Blueprint
from .config import Config as Config
from .ctx import after_this_request as after_this_request
from .ctx import copy_current_request_context as copy_current_request_context
from .ctx import has_app_context as has_app_context
from .ctx import has_request_context as has_request_context
from .globals import current_app as current_app
from .globals import g as g
from .globals import request as request
from .globals import session as session
from .helpers import abort as abort
from .helpers import flash as flash
from .helpers import get_flashed_messages as get_flashed_messages
from .helpers import get_template_attribute as get_template_attribute
from .helpers import make_response as make_response
from .helpers import redirect as redirect
from .helpers import send_file as send_file
from .helpers import send_from_directory as send_from_directory
from .helpers import stream_with_context as stream_with_context
from .helpers import url_for as url_for
from .json import jsonify as jsonify
from .signals import appcontext_popped as appcontext_popped
from .signals import appcontext_pushed as appcontext_pushed
from .signals import appcontext_tearing_down as appcontext_tearing_down
from .signals import before_render_template as before_render_template
from .signals import got_request_exception as got_request_exception
from .signals import message_flashed as message_flashed
from .signals import request_finished as request_finished
from .signals import request_started as request_started
from .signals import request_tearing_down as request_tearing_down
from .signals import template_rendered as template_rendered
from .templating import render_template as render_template
from .templating import render_template_string as render_template_string
from .templating import stream_template as stream_template
from .templating import stream_template_string as stream_template_string
from .wrappers import Request as Request
from .wrappers import Response as Response
```

*E3: tests/test_signals.py lines 1-1 (module-level)*
```python
import flask
```

*E4: src/flask/templating.py lines 1-18 (module-level)*
```python
from __future__ import annotations

import typing as t

from jinja2 import BaseLoader
from jinja2 import Environment as BaseEnvironment
from jinja2 import Template
from jinja2 import TemplateNotFound

from .ctx import AppContext
from .globals import app_ctx
from .helpers import stream_with_context
from .signals import before_render_template
from .signals import template_rendered

if t.TYPE_CHECKING:  # pragma: no cover
    from .sansio.app import App
    from .sansio.scaffold import Scaffold
```

*E5: tests/test_cli.py lines 1-32 (module-level)*
```python
# This file was part of Flask-CLI and was modified under the terms of
# its Revised BSD License. Copyright © 2015 CERN.
import importlib.metadata
import os
import platform
import ssl
import sys
import types
from functools import partial
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from flask import Blueprint
from flask import current_app
from flask import Flask
from flask.cli import AppGroup
from flask.cli import find_best_app
from flask.cli import FlaskGroup
from flask.cli import get_version
from flask.cli import load_dotenv
from flask.cli import locate_app
from flask.cli import NoAppException
from flask.cli import prepare_import
from flask.cli import run_command
from flask.cli import ScriptInfo
from flask.cli import with_appcontext

cwd = Path.cwd()
test_path = (Path(__file__) / ".." / "test_apps").resolve()
```

*E6: tests/test_async.py lines 1-11 (module-level)*
```python
import asyncio

import pytest

from flask import Blueprint
from flask import Flask
from flask import request
from flask.views import MethodView
from flask.views import View

pytest.importorskip("asgiref")
```

*E7: src/flask/ctx.py lines 1-27 (module-level)*
```python
from __future__ import annotations

import contextvars
import typing as t
from functools import update_wrapper
from types import TracebackType

from werkzeug.exceptions import HTTPException
from werkzeug.routing import MapAdapter

from . import typing as ft
from .globals import _cv_app
from .helpers import _CollectErrors
from .signals import appcontext_popped
from .signals import appcontext_pushed

if t.TYPE_CHECKING:
    import typing_extensions as te
    from _typeshed.wsgi import WSGIEnvironment

    from .app import Flask
    from .sessions import SessionMixin
    from .wrappers import Request


# a singleton sentinel value for parameter defaults
_sentinel = object()
```

*E8: tests/test_testing.py lines 1-12 (module-level)*
```python
import importlib.metadata

import click
import pytest

import flask
from flask import appcontext_popped
from flask.cli import ScriptInfo
from flask.globals import _cv_app
from flask.json import jsonify
from flask.testing import EnvironBuilder
from flask.testing import FlaskCliRunner
```

**Rubric** (PASS / FAIL / UNCERTAIN for each; write a short reason for FAIL/UNCERTAIN):

| # | Question | Label | Reason |
|---|---|---|---|
| 1 | Is every factual claim supported by the cited evidence? | | |
| 2 | Does every citation actually support the claim it is attached to? | | |
| 3 | Are the cited line ranges correct? | | |
| 4 | Are there factual claims without a citation? | | |
| 5 | (abstentions only) Did it abstain correctly, `missing` naming a real gap? | | |

---
## flask-061 (set A)

**Question:** Which other Flask modules does `flask/views.py` depend on?

**Status:** insufficient_evidence   **Missing:** The provided evidence items do not contain the content of `src/flask/views.py`, so its dependencies on other Flask modules cannot be determined.

#### Everything the model was given (for rubric item 5)

*E1: tests/test_views.py lines 1-4 (module-level)*
```python
import pytest

import flask.views
from flask.testing import FlaskClient
```

*E2: tests/test_async.py lines 1-11 (module-level)*
```python
import asyncio

import pytest

from flask import Blueprint
from flask import Flask
from flask import request
from flask.views import MethodView
from flask.views import View

pytest.importorskip("asgiref")
```

*E3: examples/javascript/js_example/__init__.py lines 1-5 (module-level)*
```python
from flask import Flask

app = Flask(__name__)

from js_example import views  # noqa: F401
```

*E4: tests/test_helpers.py lines 220-225 (TestNoImports.test_name_with_import_error)*
```python
    def test_name_with_import_error(self, modules_tmp_path):
        (modules_tmp_path / "importerror.py").write_text("raise NotImplementedError()")
        try:
            flask.Flask("importerror")
        except NotImplementedError:
            AssertionError("Flask(import_name) is importing import_name.")
```

*E5: tests/conftest.py lines 97-104 (site_packages)*
```python
@pytest.fixture
def site_packages(modules_tmp_path, monkeypatch):
    """Create a fake site-packages."""
    py_dir = f"python{sys.version_info.major}.{sys.version_info.minor}"
    rv = modules_tmp_path / "lib" / py_dir / "site-packages"
    rv.mkdir(parents=True)
    monkeypatch.syspath_prepend(os.fspath(rv))
    return rv
```

*E6: src/flask/cli.py lines 333-372 (ScriptInfo.load_app)*
```python
    def load_app(self) -> Flask:
        """Loads the Flask app (if not yet loaded) and returns it.  Calling
        this multiple times will just result in the already loaded app to
        be returned.
        """
        if self._loaded_app is not None:
            return self._loaded_app
        app: Flask | None = None
        if self.create_app is not None:
            app = self.create_app()
        else:
            if self.app_import_path:
                path, name = (
                    re.split(r":(?![\\/])", self.app_import_path, maxsplit=1) + [None]
                )[:2]
                import_name = prepare_import(path)
                app = locate_app(import_name, name)
            else:
                for path in ("wsgi.py", "app.py"):
                    import_name = prepare_import(path)
                    app = locate_app(import_name, None, raise_if_not_found=False)

                    if app is not None:
                        break

        if app is None:
            raise NoAppException(
                "Could not locate a Flask application. Use the"
                " 'flask --app' option, 'FLASK_APP' environment"
                " variable, or a 'wsgi.py' or 'app.py' file in the"
                " current directory."
            )

        if self.set_debug_flag:
            # Update the app's debug flag through the descriptor so that
            # other values repopulate as well.
            app.debug = get_debug_flag()

        self._loaded_app = app
        return app
```

*E7: tests/test_instance_config.py lines 65-77 (test_installed_module_paths)*
```python
def test_installed_module_paths(
    modules_tmp_path, modules_tmp_path_prefix, purge_module, site_packages
):
    (site_packages / "site_app.py").write_text(
        "import flask\napp = flask.Flask(__name__)\n"
    )
    purge_module("site_app")

    from site_app import app

    assert app.instance_path == os.fspath(
        modules_tmp_path / "var" / "site_app-instance"
    )
```

*E8: tests/type_check/typing_route.py lines 1-13 (module-level)*
```python
from __future__ import annotations

import typing as t
from http import HTTPStatus

from flask import Flask
from flask import jsonify
from flask import stream_template
from flask.templating import render_template
from flask.views import View
from flask.wrappers import Response

app = Flask(__name__)
```

**Rubric** (PASS / FAIL / UNCERTAIN for each; write a short reason for FAIL/UNCERTAIN):

| # | Question | Label | Reason |
|---|---|---|---|
| 1 | Is every factual claim supported by the cited evidence? | | |
| 2 | Does every citation actually support the claim it is attached to? | | |
| 3 | Are the cited line ranges correct? | | |
| 4 | Are there factual claims without a citation? | | |
| 5 | (abstentions only) Did it abstain correctly, `missing` naming a real gap? | | |

---
## flask-051 (set A)

**Question:** Which files import `flask.sessions`?

**Status:** partial   **Missing:** The provided evidence items do not show all files in the repository that might import flask.sessions, as only a subset of files are included in the evidence.

### Claim 1: tests/test_session_interface.py imports from flask.sessions
Cites: E1

*E1: tests/test_session_interface.py lines 1-3 (module-level)*
```python
import flask
from flask.globals import app_ctx
from flask.sessions import SessionInterface
```

### Claim 2: tests/test_reqctx.py imports from flask.sessions
Cites: E2

*E2: tests/test_reqctx.py lines 1-12 (module-level)*
```python
from __future__ import annotations

import collections.abc as cabc
import warnings
from concurrent import futures

import pytest

import flask
from flask.sessions import SecureCookieSessionInterface
from flask.sessions import SessionInterface
from flask.testing import FlaskClient
```

#### Everything the model was given (for rubric item 5)

*E1: tests/test_session_interface.py lines 1-3 (module-level)*
```python
import flask
from flask.globals import app_ctx
from flask.sessions import SessionInterface
```

*E2: tests/test_reqctx.py lines 1-12 (module-level)*
```python
from __future__ import annotations

import collections.abc as cabc
import warnings
from concurrent import futures

import pytest

import flask
from flask.sessions import SecureCookieSessionInterface
from flask.sessions import SessionInterface
from flask.testing import FlaskClient
```

*E3: src/flask/testing.py lines 1-24 (module-level)*
```python
from __future__ import annotations

import importlib.metadata
import typing as t
from contextlib import contextmanager
from contextlib import ExitStack
from copy import copy
from types import TracebackType
from urllib.parse import urlsplit

import werkzeug.test
from click.testing import CliRunner
from click.testing import Result
from werkzeug.test import Client
from werkzeug.wrappers import Request as BaseRequest

from .cli import ScriptInfo
from .sessions import SessionMixin

if t.TYPE_CHECKING:  # pragma: no cover
    from _typeshed.wsgi import WSGIEnvironment
    from werkzeug.test import TestResponse

    from .app import Flask
```

*E4: tests/test_testing.py lines 1-12 (module-level)*
```python
import importlib.metadata

import click
import pytest

import flask
from flask import appcontext_popped
from flask.cli import ScriptInfo
from flask.globals import _cv_app
from flask.json import jsonify
from flask.testing import EnvironBuilder
from flask.testing import FlaskCliRunner
```

*E5: tests/test_cli.py lines 1-32 (module-level)*
```python
# This file was part of Flask-CLI and was modified under the terms of
# its Revised BSD License. Copyright © 2015 CERN.
import importlib.metadata
import os
import platform
import ssl
import sys
import types
from functools import partial
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from flask import Blueprint
from flask import current_app
from flask import Flask
from flask.cli import AppGroup
from flask.cli import find_best_app
from flask.cli import FlaskGroup
from flask.cli import get_version
from flask.cli import load_dotenv
from flask.cli import locate_app
from flask.cli import NoAppException
from flask.cli import prepare_import
from flask.cli import run_command
from flask.cli import ScriptInfo
from flask.cli import with_appcontext

cwd = Path.cwd()
test_path = (Path(__file__) / ".." / "test_apps").resolve()
```

*E6: examples/tutorial/flaskr/blog.py lines 1-13 (module-level)*
```python
from flask import Blueprint
from flask import flash
from flask import g
from flask import redirect
from flask import render_template
from flask import request
from flask import url_for
from werkzeug.exceptions import abort

from .auth import login_required
from .db import get_db

bp = Blueprint("blog", __name__)
```

*E7: tests/test_async.py lines 1-11 (module-level)*
```python
import asyncio

import pytest

from flask import Blueprint
from flask import Flask
from flask import request
from flask.views import MethodView
from flask.views import View

pytest.importorskip("asgiref")
```

*E8: src/flask/globals.py lines 1-15 (module-level)*
```python
from __future__ import annotations

import typing as t
from contextvars import ContextVar

from werkzeug.local import LocalProxy

if t.TYPE_CHECKING:  # pragma: no cover
    from .app import Flask
    from .ctx import _AppCtxGlobals
    from .ctx import AppContext
    from .sessions import SessionMixin
    from .wrappers import Request

    T = t.TypeVar("T", covariant=True)
```

**Rubric** (PASS / FAIL / UNCERTAIN for each; write a short reason for FAIL/UNCERTAIN):

| # | Question | Label | Reason |
|---|---|---|---|
| 1 | Is every factual claim supported by the cited evidence? | | |
| 2 | Does every citation actually support the claim it is attached to? | | |
| 3 | Are the cited line ranges correct? | | |
| 4 | Are there factual claims without a citation? | | |
| 5 | (abstentions only) Did it abstain correctly, `missing` naming a real gap? | | |

---
## flask-072 (set A)

**Question:** How does the test client send a request to the application?

**Status:** answered   **Missing:** -

### Claim 1: The test client can send requests using methods such as get, post, and open.
Cites: E1, E2, E6

*E1: tests/test_blueprints.py lines 175-218 (test_templates_and_static)*
```python
def test_templates_and_static(test_apps):
    from blueprintapp import app

    client = app.test_client()

    rv = client.get("/")
    assert rv.data == b"Hello from the Frontend"
    rv = client.get("/admin/")
    assert rv.data == b"Hello from the Admin"
    rv = client.get("/admin/index2")
    assert rv.data == b"Hello from the Admin"

    with client.get("/admin/static/test.txt") as rv:
        assert rv.data.strip() == b"Admin File"

    with client.get("/admin/static/css/test.css") as rv:
        assert rv.data.strip() == b"/* nested file */"

    # try/finally, in case other tests use this app for Blueprint tests.
    max_age_default = app.config["SEND_FILE_MAX_AGE_DEFAULT"]
    try:
        expected_max_age = 3600
        if app.config["SEND_FILE_MAX_AGE_DEFAULT"] == expected_max_age:
            expected_max_age = 7200
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = expected_max_age

        with client.get("/admin/static/css/test.css") as rv:
            assert rv.cache_control.max_age == expected_max_age
    finally:
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = max_age_default

    with app.test_request_context():
        assert (
            flask.url_for("admin.static", filename="test.txt")
            == "/admin/static/test.txt"
        )

    with app.test_request_context():
        with pytest.raises(TemplateNotFound) as e:
            flask.render_template("missing.html")
        assert e.value.name == "missing.html"

    with flask.Flask(__name__).test_request_context():
        assert flask.render_template("nested/nested.txt") == "I'm nested"
```

*E2: tests/test_basic.py lines 710-732 (test_request_processing)*
```python
def test_request_processing(app, client):
    evts = []

    @app.before_request
    def before_request():
        evts.append("before")

    @app.after_request
    def after_request(response):
        response.data += b"|after"
        evts.append("after")
        return response

    @app.route("/")
    def index():
        assert "before" in evts
        assert "after" not in evts
        return "request"

    assert "after" not in evts
    rv = client.get("/").data
    assert "after" in evts
    assert rv == b"request|after"
```

*E6: tests/test_views.py lines 7-14 (common_test)*
```python
def common_test(app):
    c = app.test_client()

    assert c.get("/").data == b"GET"
    assert c.post("/").data == b"POST"
    assert c.put("/").status_code == 405
    meths = c.open("/", method="OPTIONS").allow
    assert meths == {"GET", "HEAD", "OPTIONS", "POST"}
```

**Rubric** (PASS / FAIL / UNCERTAIN for each; write a short reason for FAIL/UNCERTAIN):

| # | Question | Label | Reason |
|---|---|---|---|
| 1 | Is every factual claim supported by the cited evidence? | | |
| 2 | Does every citation actually support the claim it is attached to? | | |
| 3 | Are the cited line ranges correct? | | |
| 4 | Are there factual claims without a citation? | | |
| 5 | (abstentions only) Did it abstain correctly, `missing` naming a real gap? | | |

---
