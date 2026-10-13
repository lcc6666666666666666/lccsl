"""Reopen/close acceptance in real Edge; use a freshly reset disposable QA DB."""
import json
import os
import time
from pathlib import Path
import uuid

import httpx
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'.runtime/tmp')
BASE='http://127.0.0.1:8100'
OUTPUT=ROOT/'test-results/reopen-browser'


def run():
    password=json.loads((ROOT/'.runtime/qa-credentials.json').read_text(encoding='utf-8'))['password']
    OUTPUT.mkdir(parents=True,exist_ok=True)
    checks=[];errors=[]
    # Three students ensure the first four classes survive closing. Only the
    # fixed QA URL is writable; no credentials from the demonstration app.
    for name in ('student01','student02','student03'):
        with httpx.Client(base_url=BASE,headers={'Origin':BASE},trust_env=False) as client:
            result=client.post('/api/v1/auth/login',json={'username':name,'password':password})
            result.raise_for_status()
            client.headers['X-CSRF-Token']=result.json()['csrf_token']
            result=client.post('/api/v1/student/schedules/2/submit',json={'version':0,'primary':[1,2,3,10],'alternates':[4,5]},headers={'Idempotency-Key':str(uuid.uuid4())})
            result.raise_for_status()
    expect.set_options(timeout=15000)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        context=browser.new_context(viewport={'width':1440,'height':1000},locale='zh-CN')
        page=context.new_page();page.set_default_timeout(15000)
        page.on('pageerror',lambda error:errors.append(str(error)))
        network=[];started=time.monotonic()
        def trace(request,event):
            if '/api/' not in request.url:return
            network.append({'seconds':round(time.monotonic()-started,3),'method':request.method,'path':request.url.split('/api/',1)[1],'event':event})
            (OUTPUT/'network.json').write_text(json.dumps(network,ensure_ascii=False,indent=2),encoding='utf-8')
        page.on('request',lambda request:trace(request,'started'))
        page.on('response',lambda response:trace(response.request,f'HTTP {response.status}'))
        page.on('requestfailed',lambda request:trace(request,str(request.failure)))
        def login(name):
            page.goto(BASE+'/login')
            page.get_by_label('账号',exact=True).fill(name)
            page.get_by_label('密码',exact=True).fill(password)
            page.get_by_role('button',name='登录',exact=True).click()
            expect(page.get_by_role('button',name='退出',exact=True)).to_be_visible()
        def logout():
            page.get_by_role('button',name='退出',exact=True).click()
            expect(page.get_by_role('button',name='登录',exact=True)).to_be_visible()
        def close():
            page.get_by_role('button',name='结束选课',exact=True).click()
            page.get_by_role('button',name='执行结束选课',exact=True).click()
            expect(page.get_by_role('dialog',name='结束选课处理结果')).to_be_visible()
            page.get_by_role('button',name='完成',exact=True).click()
            expect(page.get_by_role('button',name='重新开启选课',exact=True)).to_be_visible()
        login('admin')
        close()
        assert page.get_by_role('button',name='重新开启选课',exact=True).count()==1
        checks.append('结束后出现重新开启按钮，历史已结束学期无此入口')
        page.get_by_role('button',name='重新开启选课',exact=True).click()
        dialog=page.get_by_role('dialog',name='重新开启选课',exact=True)
        expect(dialog).to_contain_text('此前被取消的选课不会自动恢复')
        page.screenshot(path=str(OUTPUT/'01-reopen-confirm.png'),full_page=True,animations='disabled')
        dialog.get_by_role('button',name='取消',exact=True).click()
        assert next(t for t in page.request.get(BASE+'/api/v1/semesters').json() if t['id']==2)['state']=='CLOSED'
        checks.append('确认窗口说明课表、备选与账单影响，取消无写入')
        page.get_by_role('button',name='重新开启选课',exact=True).click()
        page.route('**/api/v1/registrar/semesters/2/reopen',lambda route:route.abort())
        dialog.get_by_role('button',name='确认重新开启',exact=True).click()
        expect(dialog.locator('.el-alert--error')).to_contain_text('无法连接服务器')
        page.unroute('**/api/v1/registrar/semesters/2/reopen')
        dialog.get_by_role('button',name='确认重新开启',exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.get_by_role('button',name='结束选课',exact=True)).to_be_visible()
        page.reload()
        expect(page.get_by_text('正在接受选课',exact=True)).to_be_visible()
        page.screenshot(path=str(OUTPUT/'02-reopened.png'),full_page=True,animations='disabled')
        checks.append('网络失败详细提示、恢复后重试，刷新保留开放状态')
        logout();login('student01')
        expect(page.get_by_role('button',name='提交选课',exact=True)).to_be_enabled()
        view=page.request.get(BASE+'/api/v1/student/schedules/2').json()
        assert view['primary']==[1,2,3,4] and view['alternates']==[5]
        page.get_by_role('tab',name='费用账单').click()
        expect(page.get_by_text('此账单已作废：选课已重新开放，再次结束选课后将重新计算费用。',exact=True)).to_be_visible()
        page.screenshot(path=str(OUTPUT/'03-voided-invoice.png'),full_page=True,animations='disabled')
        checks.append('学生现有选课保留、备选去重、原账单明确标记作废')
        page.get_by_role('tab',name='课程选择').click()
        page.get_by_label('搜索课程',exact=True).fill('大学英语')
        page.get_by_role('button',name='已选首选 · 移除',exact=True).click()
        page.get_by_role('button',name='提交选课',exact=True).click()
        expect(page.get_by_text('选课提交成功，正式课表已更新',exact=True)).to_be_visible()
        page.reload()
        assert page.request.get(BASE+'/api/v1/student/schedules/2').json()['primary']==[1,2,3]
        checks.append('重新开启后学生正常退课、提交并持久化')
        logout();login('professor01')
        page.get_by_label('搜索教学班',exact=True).fill('软件测试')
        page.locator('label.el-checkbox').filter(has=page.get_by_role('checkbox',name='选择任课 软件测试')).click()
        page.get_by_role('button',name='保存任课安排',exact=True).click()
        expect(page.get_by_text('任课安排已更新',exact=True)).to_be_visible()
        checks.append('教授可重新认领原先被取消的教学班')
        logout();login('admin');close()
        logout();login('student01')
        page.get_by_role('tab',name='费用账单').click()
        expect(page.get_by_text('合计 ¥1800.00',exact=True)).to_be_visible()
        assert page.get_by_text('此账单已作废：选课已重新开放，再次结束选课后将重新计算费用。',exact=True).count()==0
        invoices=page.request.get(BASE+'/api/v1/student/invoices').json()
        assert len(invoices)==1 and invoices[0]['status']=='ACTIVE' and invoices[0]['revision']==4
        page.screenshot(path=str(OUTPUT/'04-refinalized-invoice.png'),full_page=True,animations='disabled')
        checks.append('再次结束后原账单重算为1800元，学生学期账单仍只有一份')
        assert not errors,errors
        browser.close()
    (OUTPUT/'report.json').write_text(json.dumps({'passed':len(checks),'checks':checks,'browser_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'重新开启选课浏览器验收：{len(checks)}项通过，无前端运行错误。')


if __name__=='__main__':run()
