import asyncio

from pyppeteer import launch


async def get_attr(page, selector, key):
    el = await page.querySelector(selector)
    return await page.evaluate("(el, key) => el[key]", el, key)


async def set_attr(page, selector, key, value):
    el = await page.querySelector(selector)
    await page.evaluate("(el, key, value) => el[key] = value", el, key, value)


async def set_value(page, selector, value):
    await set_attr(page, selector, "value", value)


async def wait_for_url(page, url):
    while page.url != url:
        await page.waitForNavigation()


async def generate_join_url():
    browser = await launch()
    page = await browser.newPage()
    await page.goto("https://aarhusuniversity.zoom.us/signin")

    await set_value(page, "#username", "au522953")
    await set_value(page, "#password", "***REMOVED***")
    await page.click("input[type=submit]")

    await wait_for_url(page, "https://aarhusuniversity.zoom.us/profile")

    await page.goto("https://aarhusuniversity.zoom.us/meeting/schedule")

    await set_value(page, "#topic", "Academy")
    await set_attr(page, "#option_video_host_on", "checked", True)
    await set_attr(page, "#option_video_participant_on", "checked", True)
    await set_attr(page, "#option_mute_upon_entry", "checked", False)

    await asyncio.wait([page.click("#schedule_form .submit"), page.waitForNavigation()])

    join_url = await get_attr(
        page, "a[href^='https://aarhusuniversity.zoom.us/j/']", "href"
    )

    await browser.close()

    return join_url


if __name__ == "__main__":
    print(asyncio.run(generate_join_url()))
