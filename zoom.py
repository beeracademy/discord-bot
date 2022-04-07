import asyncio
from urllib.parse import urlparse

from pyppeteer import launch


async def get_attr(page, selector, key):
    el = await page.querySelector(selector)
    return await page.evaluate("(el, key) => el[key]", el, key)


async def set_attr(page, selector, key, value):
    el = await page.querySelector(selector)
    await page.evaluate("(el, key, value) => el[key] = value", el, key, value)


async def set_value(page, selector, value):
    await set_attr(page, selector, "value", value)


async def wait_for_domain(page, domain):
    while urlparse(page.url).netloc != domain:
        await page.waitForNavigation()


async def click(page, selector):
    """
    Unlike page.click this can click elements behind a popup
    """
    await page.evaluate(
        "(selector) => document.querySelector(selector).click()", selector
    )


async def generate_join_url(username, password, headless=True):
    browser = await launch(headless=headless, args=["--no-sandbox"])
    page = await browser.newPage()
    await page.goto("https://aarhusuniversity.zoom.us/signin")

    await set_value(page, "#username", username)
    await set_value(page, "#password", password)
    await click(page, "input[type=submit]")

    await wait_for_domain(page, "aarhusuniversity.zoom.us")

    await page.goto("https://aarhusuniversity.zoom.us/meeting/schedule")

    await set_value(page, "#topic", "Academy")
    await set_attr(page, "#option_video_host_on", "checked", True)
    await set_attr(page, "#option_video_participant_on", "checked", True)
    await set_attr(page, "#option_mute_upon_entry", "checked", False)

    await asyncio.wait([click(page, "#meetingSaveButton"), page.waitForNavigation()])

    join_url = await get_attr(
        page, "a[href^='https://aarhusuniversity.zoom.us/j/']", "href"
    )

    await browser.close()

    return join_url


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("password")
    parser.add_argument("--no-headless", action="store_false", dest="headless")
    args = parser.parse_args()

    print(asyncio.run(generate_join_url(args.username, args.password, args.headless)))
