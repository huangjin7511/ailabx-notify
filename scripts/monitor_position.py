#!/usr/bin/env python3
"""
策略持仓监控脚本
监控指定策略的持仓变化，并通过企业微信发送通知
"""

import os
import json
import time
import requests
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# 配置信息
STRATEGY_IDS = ['591', '590']  # 要监控的策略ID
BASE_URL = "http://app.ailabx.com/#/pages/strategy/detail"
DATA_FILE = "position_data.json"  # 存储持仓数据的文件

# 企业微信webhook URL
WECHAT_WEBHOOK = os.getenv("WECHAT_WORK_WEBHOOK")

# 登录凭证
USERNAME = os.getenv("AILABX_USERNAME")
PASSWORD = os.getenv("AILABX_PASSWORD")


def setup_driver():
    """设置并返回Chrome浏览器驱动"""
    chrome_options = Options()
    chrome_options.add_argument('--headless')  # 无头模式
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--window-size=1920,1080')

    driver = webdriver.Chrome(options=chrome_options)
    return driver


def login(driver):
    """登录系统"""
    driver.get("http://app.ailabx.com/#/pages/login")

    # 等待登录页面加载
    wait = WebDriverWait(driver, 10)

    try:
        # 输入用户名
        username_input = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input[type='text'], input[name='username'], input[placeholder*='用户名'], input[placeholder*='账号']")
        ))
        username_input.clear()
        username_input.send_keys(USERNAME)

        # 输入密码
        password_input = driver.find_element(
            By.CSS_SELECTOR, "input[type='password'], input[name='password'], input[placeholder*='密码']"
        )
        password_input.clear()
        password_input.send_keys(PASSWORD)

        # 点击登录按钮
        login_button = driver.find_element(
            By.CSS_SELECTOR, "button[type='submit'], button:contains('登录'), .login-btn"
        )
        login_button.click()

        # 等待登录完成
        time.sleep(3)

        # 检查是否登录成功
        current_url = driver.current_url
        if "login" in current_url:
            raise Exception("登录失败，请检查用户名和密码")

        print(f"登录成功，当前URL: {current_url}")
        return True
    except Exception as e:
        print(f"登录过程中出错: {str(e)}")
        raise


def get_position_data(driver, strategy_id):
    """获取指定策略的持仓数据"""
    url = f"{BASE_URL}?id={strategy_id}"
    driver.get(url)

    # 等待页面加载完成
    time.sleep(5)

    # 尝试多种可能的定位策略来获取持仓数据
    position_data = []

    try:
        # 等待持仓表格加载
        wait = WebDriverWait(driver, 10)
        table = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "table, .position-table, .hold-table, .stock-table")
        ))

        # 获取表格行
        rows = table.find_elements(By.CSS_SELECTOR, "tbody tr, .table-body tr, tr.data-row")

        for row in rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) >= 2:
                # 假设第一列是股票代码，第二列是股票名称，第三列是持仓数量
                stock_code = cells[0].text.strip()
                stock_name = cells[1].text.strip()
                position = cells[2].text.strip() if len(cells) > 2 else ""

                position_data.append({
                    "code": stock_code,
                    "name": stock_name,
                    "position": position
                })
    except Exception as e:
        print(f"获取策略 {strategy_id} 的持仓数据时出错: {str(e)}")
        # 尝试通过API获取数据
        try:
            # 获取页面中的API请求
            logs = driver.get_log("performance")

            for entry in logs:
                message = json.loads(entry["message"])["message"]
                if message["method"] == "Network.responseReceived":
                    url = message["params"]["response"]["url"]
                    if "position" in url or "hold" in url or "strategy" in url:
                        print(f"发现可能的API请求: {url}")
                        # 这里可以添加处理API响应的代码
        except Exception as api_error:
            print(f"尝试获取API数据时出错: {str(api_error)}")

    return position_data


def load_previous_data():
    """加载之前保存的持仓数据"""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_current_data(data):
    """保存当前持仓数据到文件"""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def compare_positions(old_data, new_data, strategy_id):
    """比较新旧持仓数据，返回变化信息"""
    if strategy_id not in old_data:
        return {"type": "new", "message": "首次获取持仓数据"}

    old_positions = old_data[strategy_id]
    new_positions = new_data

    # 转换为字典便于比较
    old_dict = {f"{p['code']}_{p['name']}": p['position'] for p in old_positions}
    new_dict = {f"{p['code']}_{p['name']}": p['position'] for p in new_positions}

    changes = []

    # 检查新增的持仓
    for key in new_dict:
        if key not in old_dict:
            code, name = key.split('_', 1)
            changes.append(f"新增持仓: {name}({code}), 数量: {new_dict[key]}")

    # 检查减少的持仓
    for key in old_dict:
        if key not in new_dict:
            code, name = key.split('_', 1)
            changes.append(f"减少持仓: {name}({code}), 原数量: {old_dict[key]}")

    # 检查持仓数量变化
    for key in new_dict:
        if key in old_dict and new_dict[key] != old_dict[key]:
            code, name = key.split('_', 1)
            changes.append(f"持仓变化: {name}({code}), {old_dict[key]} -> {new_dict[key]}")

    if not changes:
        return {"type": "no_change", "message": "持仓无变化"}

    return {"type": "changed", "changes": changes}


def send_wechat_notification(message):
    """发送企业微信通知"""
    if not WECHAT_WEBHOOK:
        print("未配置企业微信Webhook URL，跳过通知发送")
        return

    data = {
        "msgtype": "text",
        "text": {
            "content": message
        }
    }

    try:
        response = requests.post(WECHAT_WEBHOOK, json=data)
        result = response.json()

        if result.get("errcode") == 0:
            print("企业微信通知发送成功")
        else:
            print(f"企业微信通知发送失败: {result}")
    except Exception as e:
        print(f"发送企业微信通知时出错: {str(e)}")


def format_notification_message(strategy_id, compare_result):
    """格式化通知消息"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if compare_result["type"] == "new":
        return f"""
【策略持仓监控】
策略ID: {strategy_id}
时间: {timestamp}
状态: {compare_result["message"]}
"""
    elif compare_result["type"] == "no_change":
        return None  # 无变化则不发送通知
    elif compare_result["type"] == "changed":
        changes_text = "\n".join(compare_result["changes"])
        return f"""
【策略持仓变化提醒】
策略ID: {strategy_id}
时间: {timestamp}
变化详情:
{changes_text}
"""


def main():
    """主函数"""
    if not USERNAME or not PASSWORD:
        print("错误: 未设置登录凭证，请检查环境变量 AILABX_USERNAME 和 AILABX_PASSWORD")
        return

    if not WECHAT_WEBHOOK:
        print("警告: 未设置企业微信Webhook URL，将无法发送通知")

    # 设置浏览器驱动
    driver = setup_driver()

    try:
        # 登录系统
        login(driver)

        # 加载之前的持仓数据
        previous_data = load_previous_data()

        # 当前持仓数据
        current_data = {}

        # 监控每个策略
        for strategy_id in STRATEGY_IDS:
            print(f"正在获取策略 {strategy_id} 的持仓数据...")
            position_data = get_position_data(driver, strategy_id)
            current_data[strategy_id] = position_data

            # 比较持仓变化
            compare_result = compare_positions(previous_data, position_data, strategy_id)

            # 如果有变化，发送通知
            if compare_result["type"] != "no_change":
                message = format_notification_message(strategy_id, compare_result)
                if message:
                    print(f"检测到策略 {strategy_id} 持仓变化，准备发送通知...")
                    send_wechat_notification(message)
            else:
                print(f"策略 {strategy_id} 持仓无变化")

        # 保存当前持仓数据
        save_current_data(current_data)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
