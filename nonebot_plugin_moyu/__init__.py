import re
import ssl
from pathlib import Path

import httpx
from nonebot import get_bot, get_driver, logger, on_command, require
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import Arg, CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.typing import T_State

try:
    import ujson as json  # type: ignore
except ModuleNotFoundError:
    import json

require("nonebot_plugin_apscheduler")

__plugin_meta__ = PluginMetadata(
    name="摸鱼日历",
    description="摸鱼一时爽, 一直摸鱼一直爽",
    usage="""摸鱼日历
    摸鱼日历状态
    摸鱼日历设置 小时:分钟
    摸鱼日历禁用
    """,
    type="application",
)


from nonebot_plugin_apscheduler import scheduler

subscribe = Path(__file__).parent / "subscribe.json"

subscribe_list = json.loads(subscribe.read_text("utf-8")) if subscribe.is_file() else {}

# 避免 Linux 下 HTTPS 请求失败
_ssl_context = ssl.create_default_context()
_ssl_context.set_ciphers("DEFAULT")


def save_subscribe():
    subscribe.write_text(json.dumps(subscribe_list), encoding="utf-8")


driver = get_driver()


async def get_calendar() -> bytes:
    async with httpx.AsyncClient(
        http2=True, follow_redirects=True, verify=_ssl_context
    ) as client:
        # 以下方法是一个通用方法，但是现在已经失效，保留代码，以备不时之需
        # response = await client.get(
        #     "https://api.j4u.ink/v1/store/other/proxy/remote/moyu.json"
        # )
        # if response.is_error:
        #     raise ValueError(f"摸鱼日历获取失败，错误码：{response.status_code}")
        # content = response.json()

        # # 获取公众号文章URL
        # response = await client.get(
        #     str(content['data']['articles'][-1]['url'])
        # )
        # if response.is_error:
        #     raise ValueError(f"摸鱼日历获取失败，错误码：{response.status_code}")

        # # 从返回的公众号HTML文本中提取每日摸鱼图的URL
        # urls = re.findall(r'data-src="([^"]+)"', response.text[response.text.find('今天你摸鱼了吗？'):])

        # if urls:
        #     image = await client.get(urls[0])
        #     return image.content
        response = await client.get("https://api.vvhan.com/api/moyu")
        if response.status_code not in (302, 200):
            raise ValueError(f"摸鱼日历获取失败，错误码：{response.status_code}")

        if response.status_code == 302:
            image_url = response.headers["location"]
            image = (await client.get(image_url)).content
        elif response.status_code == 200:
            image = response.content

        return image

    raise ValueError("摸鱼日历获取失败，未找到摸鱼图URL")


@driver.on_startup
async def subscribe_jobs():
    for group_id, info in subscribe_list.items():
        scheduler.add_job(
            push_calendar,
            "cron",
            args=[group_id],
            id=f"moyu_calendar_{group_id}",
            replace_existing=True,
            hour=info["hour"],
            minute=info["minute"],
            misfire_grace_time=60,  # 添加定时任务设置超时时间为60秒
        )


async def push_calendar(group_id: str):
    bot = get_bot()
    moyu_img = await get_calendar()
    await bot.send_group_msg(
        group_id=int(group_id), message=MessageSegment.image(moyu_img)
    )


def calendar_subscribe(group_id: str, hour: str, minute: str) -> None:
    subscribe_list[group_id] = {"hour": hour, "minute": minute}
    save_subscribe()
    scheduler.add_job(
        push_calendar,
        "cron",
        args=[group_id],
        id=f"moyu_calendar_{group_id}",
        replace_existing=True,
        hour=hour,
        minute=minute,
    )
    logger.debug(f"群[{group_id}]设置摸鱼日历推送时间为：{hour}:{minute}")


moyu_matcher = on_command("摸鱼日历", aliases={"摸鱼"})


@moyu_matcher.handle()
async def moyu(
    event: GroupMessageEvent, matcher: Matcher, args: Message = CommandArg()
):
    if cmdarg := args.extract_plain_text():
        if "状态" in cmdarg:
            push_state = scheduler.get_job(f"moyu_calendar_{event.group_id}")
            moyu_state = "摸鱼日历状态：\n每日推送: " + (
                "已开启" if push_state else "已关闭"
            )
            if push_state:
                group_id_info = subscribe_list[str(event.group_id)]
                moyu_state += (
                    f"\n推送时间: {group_id_info['hour']}:{group_id_info['minute']}"
                )
            await matcher.finish(moyu_state)
        elif "设置" in cmdarg or "推送" in cmdarg:
            if ":" in cmdarg or "：" in cmdarg:
                matcher.set_arg("time_arg", args)
        elif "禁用" in cmdarg or "关闭" in cmdarg:
            del subscribe_list[str(event.group_id)]
            save_subscribe()
            scheduler.remove_job(f"moyu_calendar_{event.group_id}")
            await matcher.finish("摸鱼日历推送已禁用")
        else:
            await matcher.finish("摸鱼日历的参数不正确")
    else:
        moyu_img = await get_calendar()
        await matcher.finish(MessageSegment.image(moyu_img))


@moyu_matcher.got("time_arg", prompt="请发送每日定时推送日历的时间，格式为：小时:分钟")
async def handle_time(
    event: GroupMessageEvent, state: T_State, time_arg: Message = Arg()
):
    state.setdefault("max_times", 0)
    time = time_arg.extract_plain_text()
    if any(cancel in time for cancel in ["取消", "放弃", "退出"]):
        await moyu_matcher.finish("已退出摸鱼日历推送时间设置")
    match = re.search(r"(\d*)[:：](\d*)", time)
    if match and match[1] and match[2]:
        calendar_subscribe(str(event.group_id), match[1], match[2])
        if int(match[1]) < 8:
            await moyu_matcher.send("在上午 8 时前获取的摸鱼日历可能未及时更新。")
        await moyu_matcher.finish(
            f"摸鱼日历的每日推送时间已设置为：{match[1]}:{match[2]}"
        )
    else:
        state["max_times"] += 1
        if state["max_times"] >= 3:
            await moyu_matcher.finish("你的错误次数过多，已退出摸鱼日历推送时间设置")
        await moyu_matcher.reject("设置时间失败，请输入正确的格式，格式为：小时:分钟")
