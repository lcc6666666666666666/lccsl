"""Real Edge checks on the disposable QA environment, including live closing."""
import json
import os
import time
import uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8100'
OUTPUT=ROOT/'test-results/course-status-browser'
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'.runtime/tmp')


def run():
    password=json.loads((ROOT/'.runtime/qa-credentials.json').read_text(encoding='utf-8'))['password']
    OUTPUT.mkdir(parents=True,exist_ok=True)
    checks=[];errors=[];clients=[]
    def api_user(name):
        client=httpx.Client(base_url=BASE,headers={'Origin':BASE},trust_env=False,timeout=20)
        r=client.post('/api/v1/auth/login',json={'username':name,'password':password});r.raise_for_status()
        client.headers['X-CSRF-Token']=r.json()['csrf_token'];clients.append(client)
        return client
    admin=api_user('admin');professor=api_user('professor01')
    r=professor.put('/api/v1/professor/teaching/2',json={'offering_ids':[1],'expected_ids':[1,2,3]});r.raise_for_status()
    expect.set_options(timeout=20000)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        def login(name):
            context=browser.new_context(viewport={'width':1500,'height':1050},locale='zh-CN',timezone_id='Asia/Shanghai')
            page=context.new_page();page.set_default_timeout(20000)
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(BASE+'/login')
            page.get_by_label('账号',exact=True).fill(name);page.get_by_label('密码',exact=True).fill(password)
            page.get_by_role('button',name='登录',exact=True).click()
            expect(page.get_by_role('button',name='退出',exact=True)).to_be_visible()
            return page
        student=login('student14')
        def course(name,button):
            student.get_by_label('搜索课程',exact=True).fill(name)
            student.locator('article.course-card').filter(has=student.get_by_role('heading',name=name,exact=True)).get_by_role('button',name=button,exact=True).click()
        for name in ('程序设计基础','离散数学','计算机导论','大学英语'):course(name,'加入首选')
        for name in ('数据结构','数据库原理'):course(name,'加入备选')
        student.get_by_role('button',name='提交选课',exact=True).click()
        expect(student.get_by_text('选课提交成功，正式课表已更新',exact=True)).to_be_visible()
        student.get_by_role('tab',name='选课状态',exact=True).click()
        expect(student.get_by_text('选课成功，待确认开班',exact=True)).to_have_count(4)
        expect(student.get_by_text('备选等待补位',exact=True)).to_have_count(2)
        checks.append('学生状态区分已注册待确认与备选，不把提交成功误标为最终开班')
        student.get_by_role('tab',name='我的课表',exact=True).click()
        expect(student.locator('.class-block')).to_have_count(4)
        # An external admin closes the term while the student keeps this page open.
        r=admin.post('/api/v1/registrar/semesters/2/close',json={'version':1},headers={'Idempotency-Key':str(uuid.uuid4())});r.raise_for_status()
        expect(student.locator('.class-block')).to_have_count(2)
        expect(student.locator('.stat-card').nth(1).locator('strong')).to_contain_text('2')
        checks.append('页面不刷新，结束选课后课表自动由4门变为2门')
        student.get_by_role('tab',name='选课状态',exact=True).click()
        expect(student.get_by_text('选课已取消',exact=True)).to_have_count(2)
        expect(student.get_by_text('没有有效授课教授',exact=True)).to_have_count(2)
        expect(student.get_by_text('开课成功',exact=True)).to_have_count(2)
        student.screenshot(path=str(OUTPUT/'01-student-status.png'),full_page=True,animations='disabled')
        student.get_by_role('tab',name='费用账单',exact=True).click()
        expect(student.get_by_text('合计 ¥1200.00',exact=True)).to_be_visible()
        expect(student.get_by_text('当前有效选课 2 门，已取消 2 门；当前有效账单包含 2 门。',exact=True)).to_be_visible()
        expect(student.locator('.invoice .el-table__body-wrapper tbody tr')).to_have_count(2)
        student.screenshot(path=str(OUTPUT/'02-bill-reconciliation.png'),full_page=True,animations='disabled')
        checks.append('取消原因明确展示；费用页说明2门有效、2门取消，与1200元账单一致')
        student.get_by_role('combobox',name='选择学期',exact=True).press('ArrowDown')
        student.get_by_role('option',name='上一学期 · 成绩归档',exact=True).click()
        expect(student.get_by_text('选课结束后将生成最终账单',exact=True)).to_be_visible()
        expect(student.get_by_text('合计 ¥1200.00',exact=True)).not_to_be_visible()
        student.get_by_role('combobox',name='选择学期',exact=True).press('ArrowDown')
        student.get_by_role('option',name='本学期 · 课程注册',exact=True).click()
        expect(student.get_by_text('合计 ¥1200.00',exact=True)).to_be_visible()
        checks.append('费用账单跟随顶部学期，不串用其他学期账单')
        teacher=login('professor03')
        expect(teacher.locator('.class-block')).to_have_count(0)
        teacher.get_by_role('tab',name='任课状态',exact=True).click()
        expect(teacher.get_by_text('教学班已取消',exact=True)).to_have_count(3)
        expect(teacher.get_by_text('最终选课人数不足3人',exact=True)).to_have_count(3)
        teacher.screenshot(path=str(OUTPUT/'03-professor-status.png'),full_page=True,animations='disabled')
        checks.append('教授端显示不足3人取消，已取消教学班不进入授课时间表')
        now=datetime.now(timezone.utc)
        r=admin.post('/api/v1/registrar/semesters/2/reopen',json={'version':2,'registration_open':(now-timedelta(minutes=1)).isoformat(),'registration_close':(now+timedelta(days=1)).isoformat()},headers={'Idempotency-Key':str(uuid.uuid4())});r.raise_for_status()
        expect(teacher.get_by_text('认领成功，待确认开班',exact=True)).to_have_count(3)
        teacher.get_by_role('tab',name='任课安排',exact=True).click()
        teacher.get_by_label('搜索教学班',exact=True).fill('数据结构')
        teacher.locator('label.el-checkbox').filter(has=teacher.get_by_role('checkbox',name='选择任课 数据结构',exact=True)).click()
        teacher.get_by_role('button',name='保存任课安排',exact=True).click()
        expect(teacher.get_by_text('任课安排已更新',exact=True)).to_be_visible()
        teacher.get_by_role('tab',name='任课状态',exact=True).click()
        expect(teacher.get_by_text('已取消任课',exact=True)).to_have_count(1)
        expect(teacher.get_by_text('教授主动取消任课',exact=True)).to_be_visible()
        teacher.reload()
        teacher.get_by_role('tab',name='任课状态',exact=True).click()
        expect(teacher.get_by_text('教授主动取消任课',exact=True)).to_be_visible()
        checks.append('重新开启自动恢复待确认状态；主动退出记录刷新后仍可查看')
        student.bring_to_front()
        student.get_by_role('tab',name='选课状态',exact=True).click()
        expect(student.get_by_text('没有有效授课教授',exact=True)).to_have_count(2)
        expect(student.get_by_text('选课成功，待确认开班',exact=True)).to_have_count(2)
        checks.append('重新开启后仍可查原取消原因，并同步保留课程的当前状态')
        student.get_by_role('tab',name='课程选择',exact=True).click()
        course('离散数学','加入首选')
        with student.expect_response(lambda r:r.url.endswith('/student/registration-status/2') and r.status==200):
            student.wait_for_timeout(5500)
        expect(student.locator('aside .selected-row')).to_have_count(5)  # 3 local choices + 2 alternates
        other=api_user('student14')
        version=other.get('/api/v1/student/schedules/2').json()['version']
        r=other.post('/api/v1/student/schedules/2/submit',json={'version':version,'primary':[1,4,10],'alternates':[]},headers={'Idempotency-Key':str(uuid.uuid4())});r.raise_for_status()
        expect(student.get_by_text('选课结果已更新，已保留你尚未提交的选择。为避免覆盖最新课表，请先取消未保存的修改，再按最新状态调整。',exact=True)).to_be_visible()
        student.get_by_role('button',name='提交选课',exact=True).click()
        expect(student.get_by_text('本次修改尚未提交：课表已有新的处理结果。请先取消未保存的修改、查看最新选课状态，再重新调整。',exact=True)).to_be_visible()
        assert other.get('/api/v1/student/schedules/2').json()['primary']==[1,4,10]
        checks.append('后台刷新保留未提交选择，旧页面不能覆盖其他页面已提交的最新课表')
        assert not errors,errors
        browser.close()
    for client in clients:client.close()
    (OUTPUT/'report.json').write_text(json.dumps({'passed':len(checks),'checks':checks,'browser_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'课程状态浏览器验收：{len(checks)}项通过，无前端运行错误。')


if __name__=='__main__':run()
