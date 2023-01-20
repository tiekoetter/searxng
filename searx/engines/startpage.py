# SPDX-License-Identifier: AGPL-3.0-or-later
# lint: pylint
"""Startpage (Web)

"""

import re
from time import time

from unicodedata import normalize, combining
from datetime import datetime, timedelta
from collections import OrderedDict

from dateutil import parser
from lxml import html

import babel

from searx.network import get
from searx.utils import extract_text, eval_xpath
from searx.exceptions import (
    SearxEngineResponseException,
    SearxEngineCaptchaException,
)


# about
about = {
    "website": 'https://startpage.com',
    "wikidata_id": 'Q2333295',
    "official_api_documentation": None,
    "use_official_api": False,
    "require_api_key": False,
    "results": 'HTML',
}

# engine dependent config
categories = ['general', 'web']

paging = True
number_of_results = 5

safesearch = True
filter_mapping = {0: '0', 1: '1', 2: '1'}

time_range_support = True
time_range_dict = {'day': 'd', 'week': 'w', 'month': 'm', 'year': 'y'}

supported_properties_url = 'https://www.startpage.com/do/settings'

# search-url
base_url = 'https://www.startpage.com/'
search_url = base_url + 'sp/search'

# specific xpath variables
# ads xpath //div[@id="results"]/div[@id="sponsored"]//div[@class="result"]
# not ads: div[@class="result"] are the direct childs of div[@id="results"]
results_xpath = '//div[@class="w-gl__result__main"]'
link_xpath = './/a[@class="w-gl__result-title result-link"]'
content_xpath = './/p[@class="w-gl__description"]'

# timestamp of the last fetch of 'sc' code
sc_code_ts = 0
sc_code = ''


def raise_captcha(resp):

    if str(resp.url).startswith('https://www.startpage.com/sp/captcha'):
        # suspend CAPTCHA for 7 days
        raise SearxEngineCaptchaException(suspended_time=7 * 24 * 3600)


def get_sc_code(headers):
    """Get an actual `sc` argument from startpage's home page.

    Startpage puts a `sc` argument on every link.  Without this argument
    startpage considers the request is from a bot.  We do not know what is
    encoded in the value of the `sc` argument, but it seems to be a kind of a
    *time-stamp*.  This *time-stamp* is valid for a few hours.

    This function scrap a new *time-stamp* from startpage's home page every hour
    (3000 sec).

    """

    global sc_code_ts, sc_code  # pylint: disable=global-statement

    if time() > (sc_code_ts + 3000):
        logger.debug("query new sc time-stamp ...")

        resp = get(base_url, headers=headers)
        raise_captcha(resp)
        dom = html.fromstring(resp.text)

        try:
            # <input type="hidden" name="sc" value="...">
            sc_code = eval_xpath(dom, '//input[@name="sc"]/@value')[0]
        except IndexError as exc:
            # suspend startpage API --> https://github.com/searxng/searxng/pull/695
            raise SearxEngineResponseException(
                suspended_time=7 * 24 * 3600, message="PR-695: query new sc time-stamp failed!"
            ) from exc

        sc_code_ts = time()
        logger.debug("new value is: %s", sc_code)

    return sc_code


def get_engine_locale(language):

    if language == 'all':
        language = 'en-US'
    locale = babel.Locale.parse(language, sep='-')

    engine_language = supported_properties['languages'].get(locale.language)
    if not engine_language:
        logger.debug("startpage does NOT support language: %s", locale.language)

    engine_region = None
    if locale.territory:
        engine_region = supported_properties['regions'].get(locale.language + '-' + locale.territory)
    if not engine_region:
        logger.debug("no region in selected (only lang: '%s'), using region 'all'", language)
        engine_region = 'all'

    logger.debug(
        "UI language: %s --> engine language: %s // engine region: %s", language, engine_language, engine_region
    )
    return locale, engine_language, engine_region


def request(query, params):

    locale, engine_language, engine_region = get_engine_locale(params['language'])

    # prepare HTTP headers
    ac_lang = locale.language
    if locale.territory:
        ac_lang = "%s-%s,%s;q=0.5" % (locale.language, locale.territory, locale.language)
    logger.debug("headers.Accept-Language --> %s", ac_lang)
    params['headers']['Accept-Language'] = ac_lang
    params['headers']['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'

    # build arguments
    args = {
        'query': query,
        'cat': 'web',
        't': 'device',
        'sc': get_sc_code(params['headers']),  # hint: this func needs HTTP headers
        'with_date': time_range_dict.get(params['time_range'], ''),
    }

    if engine_language:
        args['language'] = engine_language
        args['lui'] = engine_language

    if params['pageno'] == 1:
        args['abp'] = ['-1', '-1']

    else:
        args['page'] = params['pageno']
        args['abp'] = '-1'

    # build cookie
    lang_homepage = 'english'
    cookie = OrderedDict()
    cookie['date_time'] = 'world'
    cookie['disable_family_filter'] = filter_mapping[params['safesearch']]
    cookie['disable_open_in_new_window'] = '0'
    cookie['enable_post_method'] = '1'  # hint: POST
    cookie['enable_proxy_safety_suggest'] = '1'
    cookie['enable_stay_control'] = '1'
    cookie['instant_answers'] = '1'
    cookie['lang_homepage'] = 's/device/%s/' % lang_homepage
    cookie['num_of_results'] = '10'
    cookie['suggestions'] = '1'
    cookie['wt_unit'] = 'celsius'

    if engine_language:
        cookie['language'] = engine_language
        cookie['language_ui'] = engine_language

    if engine_region:
        cookie['search_results_region'] = engine_region

    params['cookies']['preferences'] = 'N1N'.join(["%sEEE%s" % x for x in cookie.items()])
    logger.debug('cookie preferences: %s', params['cookies']['preferences'])
    params['method'] = 'POST'

    logger.debug("data: %s", args)
    params['data'] = args

    params['url'] = search_url

    return params


# get response from search-request
def response(resp):
    results = []
    dom = html.fromstring(resp.text)

    # parse results
    for result in eval_xpath(dom, results_xpath):
        links = eval_xpath(result, link_xpath)
        if not links:
            continue
        link = links[0]
        url = link.attrib.get('href')

        # block google-ad url's
        if re.match(r"^http(s|)://(www\.)?google\.[a-z]+/aclk.*$", url):
            continue

        # block startpage search url's
        if re.match(r"^http(s|)://(www\.)?startpage\.com/do/search\?.*$", url):
            continue

        title = extract_text(link)

        if eval_xpath(result, content_xpath):
            content = extract_text(eval_xpath(result, content_xpath))
        else:
            content = ''

        published_date = None

        # check if search result starts with something like: "2 Sep 2014 ... "
        if re.match(r"^([1-9]|[1-2][0-9]|3[0-1]) [A-Z][a-z]{2} [0-9]{4} \.\.\. ", content):
            date_pos = content.find('...') + 4
            date_string = content[0 : date_pos - 5]
            # fix content string
            content = content[date_pos:]

            try:
                published_date = parser.parse(date_string, dayfirst=True)
            except ValueError:
                pass

        # check if search result starts with something like: "5 days ago ... "
        elif re.match(r"^[0-9]+ days? ago \.\.\. ", content):
            date_pos = content.find('...') + 4
            date_string = content[0 : date_pos - 5]

            # calculate datetime
            published_date = datetime.now() - timedelta(days=int(re.match(r'\d+', date_string).group()))

            # fix content string
            content = content[date_pos:]

        if published_date:
            # append result
            results.append({'url': url, 'title': title, 'content': content, 'publishedDate': published_date})
        else:
            # append result
            results.append({'url': url, 'title': title, 'content': content})

    # return results
    return results


def _fetch_engine_properties(resp, engine_properties):

    # startpage's language & region selectors are a mess.
    #
    # regions:
    #   in the list of regions there are tags we need to map to common
    #   region tags:
    #   - pt-BR_BR --> pt_BR
    #   - zh-CN_CN --> zh_Hans_CN
    #   - zh-TW_TW --> zh_Hant_TW
    #   - zh-TW_HK --> zh_Hant_HK
    #   - en-GB_GB --> en_GB
    #   and there is at least one tag with a three letter language tag (ISO 639-2)
    #   - fil_PH --> fil_PH
    #
    # languages:
    #
    #   The displayed name in startpage's settings page depend on the location
    #   of the IP when the 'Accept-Language' HTTP header is unset (in tha
    #   language update script we use "en-US,en;q=0.5" to get uniform names
    #   independent from the IP).
    #
    #   Each option has a displayed name and a value, either of which
    #   may represent the language name in the native script, the language name
    #   in English, an English transliteration of the native name, the English
    #   name of the writing script used by the language, or occasionally
    #   something else entirely.

    dom = html.fromstring(resp.text)

    # regions

    sp_region_names = []
    for option in dom.xpath('//form[@name="settings"]//select[@name="search_results_region"]/option'):
        sp_region_names.append(option.get('value'))

    for sp_region_tag in sp_region_names:
        if sp_region_tag == 'all':
            continue
        if '-' in sp_region_tag:
            l, r = sp_region_tag.split('-')
            r = r.split('_')[-1]
            locale = babel.Locale.parse(l + '_' + r, sep='_')
        else:
            locale = babel.Locale.parse(sp_region_tag, sep='_')

        region_tag = locale.language + '-' + locale.territory
        # print("internal: %s --> engine: %s" % (region_tag, sp_region_tag))
        engine_properties['regions'][region_tag] = sp_region_tag

    # languages

    catalog_engine2code = {name.lower(): lang_code for lang_code, name in babel.Locale('en').languages.items()}

    # get the native name of every language known by babel

    for lang_code in filter(lambda lang_code: lang_code.find('_') == -1, babel.localedata.locale_identifiers()):
        native_name = babel.Locale(lang_code).get_language_name().lower()
        # add native name exactly as it is
        catalog_engine2code[native_name] = lang_code

        # add "normalized" language name (i.e. français becomes francais and español becomes espanol)
        unaccented_name = ''.join(filter(lambda c: not combining(c), normalize('NFKD', native_name)))
        if len(unaccented_name) == len(unaccented_name.encode()):
            # add only if result is ascii (otherwise "normalization" didn't work)
            catalog_engine2code[unaccented_name] = lang_code

    # values that can't be determined by babel's languages names

    catalog_engine2code.update(
        {
            'english_uk': 'en',
            # traditional chinese used in ..
            'fantizhengwen': 'zh_Hant',
            # Korean alphabet
            'hangul': 'ko',
            # Malayalam is one of 22 scheduled languages of India.
            'malayam': 'ml',
            'norsk': 'nb',
            'sinhalese': 'si',
        }
    )

    for option in dom.xpath('//form[@name="settings"]//select[@name="language"]/option'):
        engine_lang = option.get('value')
        name = extract_text(option).lower()

        lang_code = catalog_engine2code.get(engine_lang)
        if lang_code is None:
            lang_code = catalog_engine2code[name]

        # print("internal: %s --> engine: %s" % (lang_code, engine_lang))
        engine_properties['languages'][lang_code] = engine_lang

    return engine_properties
