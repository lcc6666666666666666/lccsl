"""Verify the admin -> professor -> student grading flow in disposable QA."""
import json
import os
import uuid
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8100'
OUTPUT=ROOT/'test-results/semester-completion-browser'
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'.runtime/tmp')


def run():
    password=json.loads((ROOT/'.runtime/qa-credentials.json').read_text(encoding='utf-8'))['password']
    OUTPUT.mkdir(parents=True,exist_ok=True)
    checks=[];errors=[]
    with httpx.Client(base_url=BASE,headers={'Origin':BASE},trust_env=False,timeout=20) as admin_api:
        r=admin_api.post('/api/v1/auth/login',json={'username':'admin','password':password});r.raise_for_status()
        admin_api.headers['X-CSRF-Token']=r.json()['csrf_token']
        r=admin_api.post('/api/v1/registrar/semesters/2/close',json={'version':1},headers={'Idempotency-Key':str(uuid.uuid4())});r.raise_for_status()
        expect.set_options(timeout=20000)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            def login(name):
                context=browser.new_context(viewport={'width':1500,'height':1050},locale='zh-CN',timezone_id='Asia/Shanghai')
                page=context.new_page();page.set_default_timeout(20000)
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(BASE+'/login',wait_until='domcontentloaded')
                page.get_by_label('账号',exact=True).fill(name);page.get_by_label('密码',exact=True).fill(password)
                page.get_by_role('button',name='登录',exact=True).click()
                expect(page.get_by_role('button',name='退出',exact=True)).to_be_visible()
                return page
            teacher=login('professor01')
            course=teacher.get_by_role('row').filter(has=teacher.get_by_text('CS101 · CUR-01',exact=True))
            expect(course.get_by_text('已确认开班',exact=True)).to_be_visible()
            course.get_by_role('button',name='名单与成绩',exact=True).click()
            dialog=teacher.get_by_role('dialog',name='程序设计基础 · 名单与成绩',exact=True)
            grade=dialog.get_by_role('combobox',name='学生18的成绩',exact=True)
            grade_select=dialog.locator('.el-select').filter(has=teacher.get_by_role('combobox',name='学生18的成绩',exact=True))
            expect(grade).to_be_disabled()
            expect(dialog).to_contain_text('管理员可在“学期与选课”中点击“结束学期，开放成绩录入”')
            teacher.screenshot(path=str(OUTPUT/'01-awaiting-completion.png'),full_page=True,animations='disabled')
            checks.append('结束选课显示已确认开班，名单说明待结课、计划日期及管理员操作入口')
            admin=login('admin')
            term=admin.locator('section.panel').filter(has=admin.get_by_role('heading',name='本学期 · 课程注册',exact=True))
            term.get_by_role('button',name='结束学期，开放成绩录入',exact=True).click()
            confirmation=admin.get_by_role('dialog',name='结束学期，开放成绩录入',exact=True)
            expect(confirmation).to_contain_text('结课后不能重新开启选课')
            confirmation.get_by_role('button',name='取消',exact=True).click()
            expect(confirmation).not_to_be_visible()
            assert next(t for t in admin_api.get('/api/v1/semesters').json() if t['id']==2)['completed'] is False
            checks.append('管理员取消结课，学期和成绩录入权限保持不变')
            term.get_by_role('button',name='结束学期，开放成绩录入',exact=True).click()
            confirmation.get_by_role('button',name='确认结课',exact=True).click()
            expect(admin.get_by_text('学期已结课，教授现在可以录入成绩',exact=True)).to_be_visible()
            expect(term.get_by_text('已结课，可录入成绩',exact=True)).to_be_visible()
            expect(term.get_by_role('button',name='重新开启选课',exact=True)).to_have_count(0)
            admin.screenshot(path=str(OUTPUT/'02-admin-completed.png'),full_page=True,animations='disabled')
            checks.append('管理员立即结课并开放成绩录入，结课后不再提供重新开启选课')
            teacher.bring_to_front()
            expect(grade).to_be_enabled()
            expect(dialog).to_contain_text('学期已结课，可录入或修改')
            checks.append('教授已打开的名单自动开放成绩录入，无需退出或切换历史学期')
            grade.press('ArrowDown');teacher.get_by_role('option',name='A',exact=True).click()
            # A background read must not replace an unsaved grade or its version.
            with teacher.expect_response(lambda r:r.url.endswith('/professor/offerings/1/roster') and r.request.method=='GET'):
                pass
            expect(grade_select.get_by_text('A',exact=True)).to_be_visible()
            dialog.get_by_role('button',name='保存成绩',exact=True).click()
            expect(teacher.get_by_text('成绩已保存',exact=True)).to_be_visible()
            teacher.screenshot(path=str(OUTPUT/'03-professor-saved-grade.png'),full_page=True,animations='disabled')
            dialog.get_by_role('button',name='关闭',exact=True).click()
            course.get_by_role('button',name='名单与成绩',exact=True).click()
            expect(grade_select.get_by_text('A',exact=True)).to_be_visible()
            checks.append('后台刷新保留未保存的A成绩，保存后重新打开名单仍为A')
            student=login('student18')
            student.get_by_role('tab',name='学期成绩单',exact=True).click()
            report_select=student.locator('.el-select').filter(has=student.get_by_role('combobox',name='成绩学期',exact=True))
            expect(report_select.get_by_text('本学期 · 课程注册',exact=True)).to_be_visible()
            row=student.get_by_role('row').filter(has=student.get_by_role('cell',name='程序设计基础',exact=True))
            expect(row.get_by_text('A',exact=True)).to_be_visible()
            student.screenshot(path=str(OUTPUT/'04-student-report.png'),full_page=True,animations='disabled')
            checks.append('学生默认成绩单切换到刚结课学期，可查看程序设计基础的A成绩')
            admin.bring_to_front();admin.get_by_role('link',name='操作记录',exact=True).click()
            expect(admin.get_by_role('cell',name='结束学期并开放成绩录入',exact=True)).to_be_visible()
            checks.append('管理员结课操作进入审计记录')
            assert not errors,errors
            browser.close()
    report={'passed':len(checks),'checks':checks,'page_errors':errors,'database':'isolated _test schema'}
    (OUTPUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':run()
