"""Admin recovery UI and original-password login on the isolated QA database."""
import json
import os
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8100'
OUTPUT=ROOT/'test-results/account-reactivation-browser'
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'.runtime/tmp')


def run():
    password=json.loads((ROOT/'.runtime/qa-credentials.json').read_text(encoding='utf-8'))['password']
    OUTPUT.mkdir(parents=True,exist_ok=True)
    checks=[];errors=[]
    expect.set_options(timeout=20000)
    with httpx.Client(base_url=BASE,headers={'Origin':BASE},trust_env=False,timeout=20) as old:
        r=old.post('/api/v1/auth/login',json={'username':'student04','password':password});r.raise_for_status()
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            def login(name):
                context=browser.new_context(viewport={'width':1500,'height':1000},locale='zh-CN',timezone_id='Asia/Shanghai')
                page=context.new_page();page.set_default_timeout(20000)
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(BASE+'/login')
                page.get_by_label('账号',exact=True).fill(name)
                page.get_by_label('密码',exact=True).fill(password)
                page.get_by_role('button',name='登录',exact=True).click()
                expect(page.get_by_role('button',name='退出',exact=True)).to_be_visible()
                return page
            admin=login('admin')
            def row(name):
                return admin.get_by_role('row').filter(has=admin.get_by_role('cell',name=name,exact=True))
            def dialog():
                return admin.get_by_role('dialog',name='确认恢复账号',exact=True)
            for tab,name in [('students','student04'),('professors','professor01')]:
                admin.goto(f'{BASE}/registrar/{tab}')
                admin.get_by_label('搜索人员',exact=True).fill(name)
                expect(row(name)).to_have_count(1)
                expect(row(name).get_by_role('button',name='恢复账号',exact=True)).to_have_count(0)
                row(name).get_by_role('button',name='停用',exact=True).click()
                admin.get_by_role('button',name='确认停用',exact=True).click()
                expect(row(name)).to_have_count(0)
                admin.locator('label.el-checkbox').filter(has=admin.get_by_role('checkbox',name='显示已停用人员',exact=True)).click()
                expect(admin.get_by_role('checkbox',name='显示已停用人员',exact=True)).to_be_checked()
                expect(row(name).get_by_text('已停用',exact=True)).to_be_visible()
                expect(row(name).get_by_role('button',name='编辑',exact=True)).to_be_disabled()
                row(name).get_by_role('button',name='恢复账号',exact=True).click()
                expect(dialog()).to_contain_text('停用时已释放的选课名额和任课安排需重新选择')
                dialog().get_by_role('button',name='取消',exact=True).click()
                expect(row(name).get_by_text('已停用',exact=True)).to_be_visible()
                if name=='student04':
                    assert old.get('/api/v1/auth/me').status_code==401
                checks.append(f'{name}：停用后可筛选查看恢复入口，取消恢复保持停用')
                row(name).get_by_role('button',name='恢复账号',exact=True).click()
                expect(dialog()).to_be_visible()
                admin.screenshot(path=str(OUTPUT/f'{tab}-confirmation.png'),full_page=True,animations='disabled')
                dialog().get_by_role('button',name='确认恢复',exact=True).click()
                expect(admin.get_by_text('账号已恢复，请使用原密码重新登录',exact=True)).to_be_visible()
                expect(row(name).get_by_role('button',name='编辑',exact=True)).to_be_enabled()
                expect(row(name).get_by_role('button',name='恢复账号',exact=True)).to_have_count(0)
                admin.reload()
                admin.get_by_label('搜索人员',exact=True).fill(name)
                expect(row(name).get_by_role('button',name='停用',exact=True)).to_be_enabled()
                checks.append(f'{name}：确认恢复成功，刷新后保留状态，可重新编辑或重置密码')
            assert old.get('/api/v1/auth/me').status_code==401
            student=login('student04')
            student.get_by_role('tab',name='选课状态',exact=True).click()
            expect(student.get_by_text('学生账户已停用，已释放名额',exact=True)).to_be_visible()
            r=student.request.get(BASE+'/api/v1/student/report-card?semester_id=1')
            assert r.status==200 and len(r.json()['items'])==4
            student.screenshot(path=str(OUTPUT/'student-restored.png'),full_page=True,animations='disabled')
            checks.append('学生用原密码重新登录成功，旧会话仍失效，四门历史成绩保留，已退选课程未自动占座')
            teacher=login('professor01')
            teacher.get_by_role('tab',name='任课状态',exact=True).click()
            expect(teacher.get_by_text('教授账户已停用，已撤销任课',exact=True)).to_have_count(3)
            r=teacher.request.get(BASE+'/api/v1/professor/offerings/101/roster')
            assert r.status==200 and r.json()['items']
            teacher.screenshot(path=str(OUTPUT/'professor-restored.png'),full_page=True,animations='disabled')
            checks.append('教授用原密码重新登录成功，可查看历史名单，已撤销任课记录保留且不自动认领')
            admin.bring_to_front();admin.get_by_role('link',name='操作记录',exact=True).click()
            expect(admin.get_by_role('cell',name='恢复账号',exact=True)).to_have_count(2)
            checks.append('管理员审计界面记录两次恢复账号操作')
            assert not errors,errors
            browser.close()
    report={'passed':len(checks),'checks':checks,'page_errors':errors,'database':'isolated _test schema','base_url':BASE}
    (OUTPUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    run()
