# SPDX-License-Identifier: AGPL-3.0-or-later
# lint: pylint
"""This module implements the engine loader.

Load and initialize the ``engines``, see :py:func:`load_engines` and register
:py:obj:`engine_shortcuts`.

usage::

    load_engines( settings['engines'] )

"""

import sys
import copy
from typing import Dict, List, Optional

from os.path import realpath, dirname
from babel.localedata import locale_identifiers
from searx import logger, settings
from searx.data import ENGINES_LANGUAGES
from searx.utils import load_module, match_language


logger = logger.getChild('engines')
ENGINE_DIR = dirname(realpath(__file__))
BABEL_LANGS = [
    lang_parts[0] + '-' + lang_parts[-1] if len(lang_parts) > 1 else lang_parts[0]
    for lang_parts in (lang_code.split('_') for lang_code in locale_identifiers())
]
ENGINE_DEFAULT_ARGS = {
    "engine_type": "online",
    "inactive": False,
    "disabled": False,
    "timeout": settings["outgoing"]["request_timeout"],
    "shortcut": "-",
    "categories": ["general"],
    "language_support": False,
    "paging": False,
    "safesearch": False,
    "time_range_support": False,
    "enable_http": False,
    "using_tor_proxy": False,
    "display_error_messages": True,
    "send_accept_language_header": False,
    "tokens": [],
    "about": {},
}
# set automatically when an engine does not have any tab category
OTHER_CATEGORY = 'other'


class Engine:  # pylint: disable=too-few-public-methods
    """This class is currently never initialized and only used for type hinting."""

    name: str
    engine: str
    shortcut: str
    categories: List[str]
    about: dict
    inactive: bool
    disabled: bool
    # language support, either by selecting a region or by selecting a language
    language_support: bool
    paging: bool
    safesearch: bool
    time_range_support: bool
    timeout: float


# Defaults for the namespace of an engine module, see :py:func:`load_engine`

categories = {'general': []}
engines: Dict[str, Engine] = {}
engine_shortcuts = {}
"""Simple map of registered *shortcuts* to name of the engine (or ``None``).

::

    engine_shortcuts[engine.shortcut] = engine.name

:meta hide-value:
"""


def load_engine(engine_data: dict) -> Optional[Engine]:
    """Load engine from ``engine_data``.

    :param dict engine_data:  Attributes from YAML ``settings:engines/<engine>``
    :return: initialized namespace of the ``<engine>``.

    1. create a namespace and load module of the ``<engine>``
    2. update namespace with the defaults from :py:obj:`ENGINE_DEFAULT_ARGS`
    3. update namespace with values from ``engine_data``

    If engine *is active*, return namespace of the engine, otherwise return
    ``None``.

    This function also returns ``None`` if initialization of the namespace fails
    for one of the following reasons:

    - engine name contains underscore
    - engine name is not lowercase
    - required attribute is not set :py:func:`is_missing_required_attributes`

    """
    # pylint: disable=too-many-return-statements

    engine_name = engine_data.get('name')
    if engine_name is None:
        logger.error('An engine does not have a "name" field')
        return None
    if '_' in engine_name:
        logger.error('Engine name contains underscore: "{}"'.format(engine_name))
        return None

    if engine_name.lower() != engine_name:
        logger.warn('Engine name is not lowercase: "{}", converting to lowercase'.format(engine_name))
        engine_name = engine_name.lower()
        engine_data['name'] = engine_name

    # load_module
    engine_module = engine_data.get('engine')
    if engine_module is None:
        logger.error('The "engine" field is missing for the engine named "{}"'.format(engine_name))
        return None
    try:
        engine = load_module(engine_module + '.py', ENGINE_DIR)
    except (SyntaxError, KeyboardInterrupt, SystemExit, SystemError, ImportError, RuntimeError):
        logger.exception('Fatal exception in engine "{}"'.format(engine_module))
        sys.exit(1)
    except BaseException:
        logger.exception('Cannot load engine "{}"'.format(engine_module))
        return None

    update_engine_attributes(engine, engine_data)
    set_language_attributes(engine)
    update_attributes_for_tor(engine)

    if not is_engine_active(engine):
        return None

    if is_missing_required_attributes(engine):
        return None

    set_loggers(engine, engine_name)

    if not any(cat in settings['categories_as_tabs'] for cat in engine.categories):
        engine.categories.append(OTHER_CATEGORY)

    return engine


def engine_properties_template():
    """A dictionary with languages and regions to map from SearXNG' languages &
    region tags to engine's language & region tags::

      engine_properties = {
          'type' : 'engine_properties',
          'regions': {
              # 'ca-ES' : <engine's region name>
          },
          'languages': {
              # 'ca' : <engine's language name>
          },
      }
    """
    return {
        'type': 'engine_properties',
        'regions': {},
        'languages': {},
    }


def set_loggers(engine, engine_name):
    # set the logger for engine
    engine.logger = logger.getChild(engine_name)
    # the engine may have load some other engines
    # may sure the logger is initialized
    # use sys.modules.copy() to avoid "RuntimeError: dictionary changed size during iteration"
    # see https://github.com/python/cpython/issues/89516
    # and https://docs.python.org/3.10/library/sys.html#sys.modules
    modules = sys.modules.copy()
    for module_name, module in modules.items():
        if (
            module_name.startswith("searx.engines")
            and module_name != "searx.engines.__init__"
            and not hasattr(module, "logger")
        ):
            module_engine_name = module_name.split(".")[-1]
            module.logger = logger.getChild(module_engine_name)


def update_engine_attributes(engine: Engine, engine_data):
    # set engine attributes from engine_data
    for param_name, param_value in engine_data.items():
        if param_name == 'categories':
            if isinstance(param_value, str):
                param_value = list(map(str.strip, param_value.split(',')))
            engine.categories = param_value
        elif hasattr(engine, 'about') and param_name == 'about':
            engine.about = {**engine.about, **engine_data['about']}
        else:
            setattr(engine, param_name, param_value)

    # set default attributes
    for arg_name, arg_value in ENGINE_DEFAULT_ARGS.items():
        if not hasattr(engine, arg_name):
            setattr(engine, arg_name, copy.deepcopy(arg_value))


def set_language_attributes(engine: Engine):
    # assign supported languages from json file

    supported_properties = None

    if engine.name in ENGINES_LANGUAGES:
        supported_properties = ENGINES_LANGUAGES[engine.name]

    elif engine.engine in ENGINES_LANGUAGES:
        # The key of the dictionary ENGINES_LANGUAGES is the *engine name*
        # configured in settings.xml.  When multiple engines are configured in
        # settings.yml to use the same origin engine (python module) these
        # additional engines can use the languages from the origin engine.
        # For this use the configured ``engine: ...`` from settings.yml
        supported_properties = ENGINES_LANGUAGES[engine.engine]

    if not supported_properties:
        return

    if isinstance(supported_properties, dict) and supported_properties.get('type') == 'engine_properties':
        engine.supported_properties = supported_properties
        engine.language_support = len(supported_properties['languages']) or len(supported_properties['regions'])

    else:
        # depricated: does not work for engines that do support languages
        # based on a region.
        engine.supported_languages = supported_properties
        engine.language_support = len(engine.supported_languages) > 0

        if hasattr(engine, 'language'):
            # For an engine, when there is `language: ...` in the YAML settings, the
            # engine supports only one language, in this case
            # engine.supported_languages should contains this value defined in
            # settings.yml
            if engine.language not in engine.supported_languages:
                raise ValueError(
                    "settings.yml - engine: '%s' / language: '%s' not supported" % (engine.name, engine.language)
                )

            if isinstance(engine.supported_languages, dict):
                engine.supported_languages = {engine.language: engine.supported_languages[engine.language]}
            else:
                engine.supported_languages = [engine.language]

        if not hasattr(engine, 'language_aliases'):
            engine.language_aliases = {}
        # find custom aliases for non standard language codes
        for engine_lang in engine.supported_languages:
            iso_lang = match_language(engine_lang, BABEL_LANGS, fallback=None)
            if (
                iso_lang
                and iso_lang != engine_lang
                and not engine_lang.startswith(iso_lang)
                and iso_lang not in engine.supported_languages
            ):
                engine.language_aliases[iso_lang] = engine_lang


def update_attributes_for_tor(engine: Engine) -> bool:
    if using_tor_proxy(engine) and hasattr(engine, 'onion_url'):
        engine.search_url = engine.onion_url + getattr(engine, 'search_path', '')
        engine.timeout += settings['outgoing'].get('extra_proxy_timeout', 0)


def is_missing_required_attributes(engine):
    """An attribute is required when its name doesn't start with ``_`` (underline).
    Required attributes must not be ``None``.

    """
    missing = False
    for engine_attr in dir(engine):
        if not engine_attr.startswith('_') and getattr(engine, engine_attr) is None:
            logger.error('Missing engine config attribute: "{0}.{1}"'.format(engine.name, engine_attr))
            missing = True
    return missing


def using_tor_proxy(engine: Engine):
    """Return True if the engine configuration declares to use Tor."""
    return settings['outgoing'].get('using_tor_proxy') or getattr(engine, 'using_tor_proxy', False)


def is_engine_active(engine: Engine):
    # check if engine is inactive
    if engine.inactive is True:
        return False

    # exclude onion engines if not using tor
    if 'onions' in engine.categories and not using_tor_proxy(engine):
        return False

    return True


def register_engine(engine: Engine):
    if engine.name in engines:
        logger.error('Engine config error: ambiguous name: {0}'.format(engine.name))
        sys.exit(1)
    engines[engine.name] = engine

    if engine.shortcut in engine_shortcuts:
        logger.error('Engine config error: ambiguous shortcut: {0}'.format(engine.shortcut))
        sys.exit(1)
    engine_shortcuts[engine.shortcut] = engine.name

    for category_name in engine.categories:
        categories.setdefault(category_name, []).append(engine)


def load_engines(engine_list):
    """usage: ``engine_list = settings['engines']``"""
    engines.clear()
    engine_shortcuts.clear()
    categories.clear()
    categories['general'] = []
    for engine_data in engine_list:
        engine = load_engine(engine_data)
        if engine:
            register_engine(engine)
    return engines
