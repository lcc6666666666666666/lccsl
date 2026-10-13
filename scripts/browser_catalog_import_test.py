"""Edge acceptance on the disposable QA database, never on demonstration data.

Run after qa_environment.py start --users 0 --instances 2 --workers 2.
"""
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'.runtime/tmp')
OUTPUT=ROOT/'test-results/catalog-import-browser'
OUTPUT.mkdir(parents=True,exist_ok=True)
PASSWORD=json.loads((ROOT/'.runtime/qa-credentials.json').read_text(encoding='utf-8'))['password']
BASE='http://127.0.0.1:8100'


def run():
    checks=[];errors=[]
    expect.set_options(timeout=15000)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        context=browser.new_context(viewport={'width':1440,'height':1000},locale='zh-CN')
        page=context.new_page();page.set_default_timeout(15000)
        page.on('pageerror',lambda e:errors.append(str(e)))
        requests={}; timings=[]
        page.on('request',lambda r:requests.update({r:time.monotonic()}))
        def timing(r):
            timings.append({'path':urlsplit(r.url).path,'status':r.status,'seconds':round(time.monotonic()-requests.get(r.request,time.monotonic()),3)})
            (OUTPUT/'timings.json').write_text(json.dumps(timings,ensure_ascii=False,indent=2),encoding='utf-8')
        page.on('response',timing)
        def login(username):
            page.goto(BASE+'/login')
            page.get_by_label('账号',exact=True).fill(username)
            page.get_by_label('密码',exact=True).fill(PASSWORD)
            page.get_by_role('button',name='登录',exact=True).click()
            expect(page.get_by_role('button',name='退出',exact=True)).to_be_visible()
        login('admin')
        page.get_by_role('link',name='课程文件导入',exact=True).click()
        expect(page.get_by_role('heading',name='课程文件导入')).to_be_visible()
        expect(page.get_by_text('尚未导入文件目录',exact=True)).to_be_visible()
        page.screenshot(path=str(OUTPUT/'01-import-page.png'),full_page=True)
        with page.expect_download() as event:
            page.get_by_role('button',name='下载当前学期示例').click()
        download=event.value
        downloaded=OUTPUT/'downloaded-template.json'
        download.save_as(downloaded)
        document=json.loads(downloaded.read_text(encoding='utf-8'))
        assert document['semester_code']=='CURRENT'
        assert len(document['courses'])==len(document['offerings'])==2
        checks.append('管理员导航、当前学期JSON模板下载')
        page.get_by_label('课程目录文件',exact=True).set_input_files(downloaded)
        expect(page.get_by_role('button',name='一键导入',exact=True)).to_be_enabled()
        page.get_by_role('button',name='仅校验预览',exact=True).click()
        expect(page.get_by_text('校验通过，以下为预计结果；尚未写入数据库',exact=True)).to_be_visible()
        assert page.request.get(BASE+'/api/v1/registrar/catalog/source').json()['mode']=='external'
        checks.append('预览显示课程、课时且不写入目录')
        page.get_by_role('button',name='一键导入',exact=True).click()
        expect(page.get_by_text('导入完成，课程目录已保存',exact=True)).to_be_visible()
        expect(page.get_by_text('文件目录已启用',exact=True)).to_be_visible()
        expect(page.locator('.stat-card').nth(0).locator('strong')).to_have_text('2')
        expect(page.get_by_role('cell',name='Web应用开发进阶',exact=True)).to_be_visible()
        page.screenshot(path=str(OUTPUT/'02-import-success.png'),full_page=True)
        checks.append('单击导入、完整结果与教授分配提醒')
        page.get_by_role('button',name='一键导入',exact=True).dblclick()
        expect(page.locator('.stat-card').nth(0).locator('strong')).to_have_text('0')
        expect(page.locator('.stat-card').nth(1).locator('strong')).to_have_text('0')
        page.reload()
        expect(page.get_by_text('文件目录已启用',exact=True)).to_be_visible()
        checks.append('重复点击不重复新增，页面刷新保留文件目录状态')
        document['offerings'][0]['meetings'][0]['end']='13:00'
        document['courses'][0]['credits']='3.25'
        page.get_by_label('课程目录文件',exact=True).set_input_files({
            'name':'错误课程目录.json','mimeType':'application/json','buffer':json.dumps(document,ensure_ascii=False).encode()})
        page.get_by_role('button',name='一键导入',exact=True).click()
        expect(page.get_by_role('heading',name='请修改以下位置')).to_be_visible()
        expect(page.get_by_role('cell',name='课程 · 第1条 · 学分',exact=True)).to_be_visible()
        expect(page.get_by_text('小数位数超限：学分最多1位，费用最多2位',exact=True)).to_be_visible()
        page.get_by_role('button',name='刷新状态').click()
        expect(page.get_by_role('heading',name='请修改以下位置')).to_be_visible()
        page.screenshot(path=str(OUTPUT/'03-field-errors.png'),full_page=True)
        checks.append('文件错误精确到记录与字段，刷新状态不清除错误')
        page.get_by_label('课程目录文件',exact=True).set_input_files(downloaded)
        page.route('**/api/v1/registrar/catalog/import',lambda route:route.abort())
        page.get_by_role('button',name='一键导入',exact=True).click()
        expect(page.locator('.el-alert').filter(has_text='无法连接服务器')).to_be_visible()
        page.unroute('**/api/v1/registrar/catalog/import')
        page.get_by_role('button',name='一键导入',exact=True).click()
        expect(page.get_by_text('导入完成，课程目录已保存',exact=True)).to_be_visible()
        checks.append('网络失败明确提示，恢复后安全重试')
        for port in (8100,8101):
            for _ in range(5):
                response=page.request.get(f'http://127.0.0.1:{port}/api/v1/catalog/offerings?semester_id=2&q=EXT101')
                assert response.status==200 and response.json()['items'][0]['name']=='Python应用开发'
        checks.append('两个API实例、每实例两个工作进程共享新目录')
        page.get_by_role('button',name='退出',exact=True).click()
        expect(page.get_by_role('button',name='登录',exact=True)).to_be_visible()
        login('student01')
        assert page.get_by_role('link',name='课程文件导入',exact=True).count()==0
        page.get_by_label('搜索课程',exact=True).fill('Web应用开发进阶')
        expect(page.get_by_role('heading',name='Web应用开发进阶',exact=True)).to_be_visible()
        expect(page.get_by_text('Python应用开发',exact=False)).to_be_visible()
        page.screenshot(path=str(OUTPUT/'04-student-catalog.png'),full_page=True)
        report=page.request.get(BASE+'/api/v1/student/report-card').json()
        assert len(report['items'])==4
        checks.append('学生可见新课程与先修要求，原历史成绩保留，学生没有导入菜单')
        context.close();browser.close()
    assert not errors,errors
    result={'passed':checks,'browser_errors':errors,'environment':'独立QA数据库 / Edge / 1440x1000'}
    (OUTPUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    run()
