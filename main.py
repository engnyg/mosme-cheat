import asyncio
import os
import re
import sys

import fitz
from dotenv import load_dotenv
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

ACCOUNT = os.getenv("MOSME_ACCOUNT")
PASSWORD = os.getenv("MOSME_PASSWORD")
TARGET_URL = "https://www.mosme.net/uclasslist/uclassdetail_examV2/d7dc4221-0353-41c9-9a43-00955dba443c?subgroupcode=243940"
LOGIN_URL = "https://bao.ipoe.cc/Member/Login?ReturnUrl=https%3a%2f%2fwww.mosme.net"
PDF_PATH = "028003A11.pdf"


def extract_answers_from_pdf(pdf_path):
    """從 PDF 擷取所有章節的答案 {section_idx: {qnum: answer(1-4)}}"""
    doc = fitz.open(pdf_path)
    raw = []
    for pg in range(doc.page_count):
        text = doc[pg].get_text()
        for qnum, ans in re.findall(r"(\d+)\.\s*\((\d)\)", text):
            q, a = int(qnum), int(ans)
            if 1 <= q <= 999 and 1 <= a <= 4:
                raw.append((q, a))

    # 依題號重置來切分章節
    sections = []
    sec = []
    prev_q = 0
    for q, a in raw:
        if q <= prev_q and sec:
            sections.append(sec)
            sec = []
        sec.append((q, a))
        prev_q = q
    if sec:
        sections.append(sec)

    return {i: {q: a for q, a in sec} for i, sec in enumerate(sections)}


async def auto_answer(page, all_sections):
    """偵測測驗頁面的題目並自動作答 - 從頁面 JS 資料模型找正確答案"""
    print("[*] 開始分析測驗頁面 ...")
    await page.wait_for_timeout(3000)

    # 嘗試從頁面的 JS 資料模型中直接取得正確答案
    js_data = await page.evaluate("""
        () => {
            const result = {};

            // 1. 檢查 Knockout.js ViewModel
            try {
                const body = document.body;
                const koCtx = ko && ko.dataFor && ko.dataFor(body);
                if (koCtx) {
                    result.knockout = JSON.stringify(Object.keys(koCtx)).substring(0, 500);
                    // 嘗試取得題目資料
                    const unwrap = (v) => typeof v === 'function' ? v() : v;
                    for (const key of Object.keys(koCtx)) {
                        const val = unwrap(koCtx[key]);
                        if (Array.isArray(val) && val.length > 0) {
                            const first = val[0];
                            if (first && (first.Answer !== undefined || first.answer !== undefined || first.ans !== undefined || first.CorrectAnswer !== undefined)) {
                                result.answerKey = key;
                                result.answerData = JSON.stringify(val.slice(0, 3)).substring(0, 1000);
                                result.totalQs = val.length;
                            }
                        }
                    }
                }
            } catch(e) { result.koError = e.message; }

            // 2. 檢查全域變數
            try {
                const globals = ['examData', 'quizData', 'questionData', 'answerData', 'testData', 'vm', 'viewModel', 'app'];
                for (const g of globals) {
                    if (window[g]) {
                        result['global_' + g] = JSON.stringify(window[g]).substring(0, 500);
                    }
                }
            } catch(e) { result.globalError = e.message; }

            // 3. 檢查 Vue instance
            try {
                const el = document.querySelector('#app, [data-v-app], .vue-app');
                if (el && el.__vue_app__) {
                    result.vue3 = 'found';
                } else if (el && el.__vue__) {
                    const data = el.__vue__.$data;
                    result.vue2 = JSON.stringify(Object.keys(data)).substring(0, 500);
                }
            } catch(e) { result.vueError = e.message; }

            // 4. 看 .question 元素的結構
            const questions = document.querySelectorAll('.question');
            result.questionCount = questions.length;
            if (questions.length > 0) {
                const q = questions[0];
                result.q0_html = q.innerHTML.substring(0, 1000);
                result.q0_data = JSON.stringify({...q.dataset});
                // 看看選項的結構
                const opts = q.querySelectorAll('.option, [class*="option"], [class*="ans"], li, label');
                result.q0_opts = [...opts].map(o => ({
                    tag: o.tagName, cls: o.className,
                    text: o.textContent.trim().substring(0, 80),
                    data: JSON.stringify({...o.dataset})
                })).slice(0, 6);
            }

            return result;
        }
    """)
    print(f"[DIAG] JS 資料模型:")
    for k, v in js_data.items():
        print(f"  {k}: {v}")

    # 如果找到 Knockout 答案資料，直接用它
    if js_data.get("answerData"):
        print(f"\n[+] 找到答案資料！共 {js_data.get('totalQs')} 題")
        # 從 Knockout ViewModel 取得完整答案並作答
        answered = await page.evaluate("""
            () => {
                const body = document.body;
                const koCtx = ko.dataFor(body);
                const unwrap = (v) => typeof v === 'function' ? v() : v;

                // 找到含答案的陣列
                let qList = null;
                for (const key of Object.keys(koCtx)) {
                    const val = unwrap(koCtx[key]);
                    if (Array.isArray(val) && val.length > 0) {
                        const first = val[0];
                        if (first && (first.Answer !== undefined || first.answer !== undefined || first.ans !== undefined || first.CorrectAnswer !== undefined)) {
                            qList = val;
                            break;
                        }
                    }
                }
                if (!qList) return { error: 'answer list not found' };

                const questions = document.querySelectorAll('.question');
                let count = 0;
                for (let i = 0; i < questions.length && i < qList.length; i++) {
                    const qData = qList[i];
                    const correctAns = qData.Answer || qData.answer || qData.ans || qData.CorrectAnswer || '';

                    const q = questions[i];
                    const opts = q.querySelectorAll('.option, [class*="option"], li, label');

                    // 根據正確答案點擊對應選項
                    for (const opt of opts) {
                        const val = opt.dataset.value || opt.dataset.ans || opt.getAttribute('value') || '';
                        const text = opt.textContent.trim();
                        if (val === String(correctAns) || text.startsWith(String(correctAns))) {
                            opt.click();
                            count++;
                            break;
                        }
                    }
                }
                return { answered: count, total: questions.length };
            }
        """)
        print(f"[+] 已作答: {answered}")
        return

    # 透過 isanswer="1" 屬性直接找正確選項並點擊
    print("[*] 嘗試透過 isanswer 屬性自動作答 ...")
    result = await page.evaluate("""
        () => {
            const questions = document.querySelectorAll('.question');
            let answered = 0, skipped = 0;
            for (const q of questions) {
                const correct = q.querySelector('.option[isanswer="1"]');
                if (correct) {
                    const btn = correct.querySelector('.option-button');
                    if (btn) { btn.click(); answered++; }
                    else { correct.click(); answered++; }
                } else {
                    skipped++;
                }
            }
            return { answered, skipped, total: questions.length };
        }
    """)
    print(f"[+] isanswer 作答結果: {result}")
    if result.get("answered", 0) > 0:
        return

    # 後備：用 PDF 答案作答
    if not all_sections:
        print("\n[!] 無 PDF 答案資料，無法繼續。")
        return

    print("[*] 嘗試用 PDF 答案作答 ...")

    # 取得頁面題目清單（題號 → 題目 div）
    qnums = await page.evaluate("""
        () => {
            const questions = document.querySelectorAll('.question');
            return [...questions].map((q, i) => {
                const numEl = q.querySelector('.qnum');
                const num = numEl ? parseInt(numEl.textContent.trim()) : i + 1;
                return num;
            });
        }
    """)
    total_q = len(qnums)
    print(f"[*] 頁面共 {total_q} 題，題號: {qnums[:5]}...")

    # 選出題數最吻合的章節
    best_sec_idx = min(all_sections.keys(), key=lambda i: abs(max(all_sections[i].keys()) - total_q))
    sec = all_sections[best_sec_idx]
    print(f"[*] 使用章節 {best_sec_idx+1}（含 Q1-Q{max(sec.keys())}）")

    # 逐題作答
    answered = 0
    for idx, qnum in enumerate(qnums):
        ans = sec.get(qnum)
        if ans is None:
            continue
        clicked = await page.evaluate(f"""
            () => {{
                const q = document.querySelectorAll('.question')[{idx}];
                if (!q) return false;
                const opts = q.querySelectorAll('.option');
                for (const opt of opts) {{
                    const oid = opt.querySelector('.oid');
                    if (oid && oid.textContent.trim().startsWith('({ans})')) {{
                        const btn = opt.querySelector('.option-button');
                        if (btn) {{ btn.click(); return true; }}
                        opt.click(); return true;
                    }}
                }}
                return false;
            }}
        """)
        if clicked:
            answered += 1
        await page.wait_for_timeout(30)  # 避免太快被擋

    print(f"[+] PDF 答案作答完成: {answered}/{total_q}")


async def main():
    # 從 PDF 擷取答案
    print("[*] 從 PDF 擷取答案 ...")
    all_sections = extract_answers_from_pdf(PDF_PATH)
    total = sum(len(s) for s in all_sections.values())
    print(f"[+] 共 {len(all_sections)} 章節, {total} 題")
    for i in sorted(all_sections.keys()):
        sec = all_sections[i]
        print(f"    章節 {i+1}: Q1-Q{max(sec.keys())} ({len(sec)} 題)")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        # 直接前往 IPOE 登入頁面
        print("[*] 前往 IPOE 登入頁面 ...")
        await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)

        # 等待密碼欄位出現（確認登入表單已載入）
        try:
            await page.wait_for_selector('input[type="password"]', timeout=15000)
            print("[+] 登入表單已載入")
        except Exception:
            await page.screenshot(path="debug.png")
            print("[!] 找不到登入表單，已截圖 debug.png")
            return

        # 填入帳號
        account_input = await page.query_selector('input[placeholder*="帳號"], input[placeholder*="Email"], input[placeholder*="IPOE"]')
        if not account_input:
            all_inputs = await page.query_selector_all("input:visible")
            for inp in all_inputs:
                t = await inp.get_attribute("type") or "text"
                if t in ("text", "email", "tel"):
                    account_input = inp
                    break
        if account_input:
            await account_input.click()
            await account_input.fill(ACCOUNT)
            print("[+] 已填入帳號")

        # 填入密碼
        password_input = await page.query_selector('input[type="password"]')
        if password_input:
            await password_input.click()
            await password_input.fill(PASSWORD)
            print("[+] 已填入密碼")

        # 點擊登入按鈕
        login_btn = await page.query_selector('button:has-text("登入")')
        if not login_btn:
            login_btn = await page.query_selector("button[type='submit'], input[type='submit']")
        if login_btn:
            await login_btn.click()
            print("[+] 已點擊登入按鈕")

        await page.wait_for_timeout(3000)

        # 直接前往即時測評頁面
        print("[*] 前往即時測評 ...")
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        print(f"[+] 已開啟: {page.url}")

        # 等待頁面 Vue 渲染完成
        await page.wait_for_timeout(5000)

        # 監聽網路請求，看點擊時觸發了哪些 API
        api_responses = []
        page.on("response", lambda resp: api_responses.append(f"{resp.status} {resp.url}") if "mosme" in resp.url else None)

        # 找到「工業電子丙級學科」的標題元素並用 Playwright 真實點擊
        print("[*] 尋找工業電子丙級學科的觸發元素 ...")

        # 先印出周圍的 HTML 結構
        parent_html = await page.evaluate("""
            () => {
                const el = document.getElementById('quiz-0281aa7d-f0d1-4e1a-9dba-42b49159bd2e');
                if (!el) return 'quiz div not found';
                // 找到它的上一個兄弟元素（通常是觸發器）
                const prev = el.previousElementSibling;
                const parent = el.parentElement;
                return {
                    parent_tag: parent ? parent.tagName + '#' + parent.id : 'none',
                    parent_html: parent ? parent.innerHTML.substring(0, 500) : 'none',
                    prev_tag: prev ? prev.tagName : 'none',
                    prev_html: prev ? prev.outerHTML.substring(0, 300) : 'none',
                };
            }
        """)
        print(f"[*] 結構: {parent_html}")

        # 用 Playwright 直接點擊含有該文字的 list-group-item 或 a 標籤
        try:
            trigger = page.locator("a:has-text('工業電子丙級學科'), .list-group-item:has-text('工業電子丙級學科')").first
            await trigger.click()
            print("[+] 已點擊觸發元素")
        except Exception as e:
            print(f"[!] 點擊失敗: {e}")

        # 等待 API 載入
        await page.wait_for_timeout(5000)
        print(f"[*] API 請求: {api_responses[-5:] if api_responses else 'none'}")

        # 檢查展開狀態
        quiz_state = await page.evaluate("""
            () => {
                const el = document.getElementById('quiz-0281aa7d-f0d1-4e1a-9dba-42b49159bd2e');
                if (!el) return 'not found';
                return {
                    display: el.style.display,
                    classes: el.className,
                    html_len: el.innerHTML.length,
                    text: el.textContent.substring(0, 200)
                };
            }
        """)
        print(f"[*] quiz div: {quiz_state}")

        # 列出所有可用試卷，讓使用者選擇
        await page.wait_for_timeout(1000)
        quizzes = await page.evaluate("""
            () => {
                const items = document.querySelectorAll('.list-group-item-dropdown');
                return [...items].map((item, i) => {
                    const title = item.querySelector('.list-group-item-title, strong, b, h4, h5');
                    const btn = item.querySelector('a.btn-primary');
                    return {
                        index: i,
                        title: title ? title.textContent.trim() : item.textContent.trim().substring(0, 60),
                        hasBtn: !!btn,
                        btnText: btn ? btn.textContent.trim() : ''
                    };
                });
            }
        """)

        if not quizzes:
            print("[!] 找不到任何試卷，請確認頁面已正確展開。")
            return

        print("\n[*] 可用試卷：")
        for q in quizzes:
            mark = "  " if q["hasBtn"] else "  (無開始按鈕)"
            print(f"  [{q['index']+1}] {q['title']}{mark}")

        choice = input("\n請選擇試卷編號 (直接 Enter 選第 1 個): ").strip()
        selected_idx = (int(choice) - 1) if choice.isdigit() and 1 <= int(choice) <= len(quizzes) else 0
        selected = quizzes[selected_idx]
        print(f"[+] 已選擇: {selected['title']}")

        # 用 popup 監聽 + 點擊「開始測驗」
        print("[*] 點擊開始測驗（監聽 popup）...")
        exam_page = None

        # 方法1: 監聽 popup
        try:
            async with page.expect_popup(timeout=10000) as popup_info:
                await page.evaluate(f"""
                    () => {{
                        const items = document.querySelectorAll('.list-group-item-dropdown');
                        const item = items[{selected_idx}];
                        if (!item) return 'not found';
                        const btn = item.querySelector('a.btn-primary');
                        if (btn) {{ btn.click(); return 'clicked'; }}
                        return 'no button';
                    }}
                """)
            exam_page = await popup_info.value
            print(f"[+] 偵測到 popup: {exam_page.url}")
            await exam_page.wait_for_load_state("domcontentloaded", timeout=30000)
            await exam_page.wait_for_timeout(5000)
        except Exception as e:
            print(f"[*] 未偵測到 popup ({e})，檢查其他方式 ...")
            # 方法2: 檢查新分頁
            await page.wait_for_timeout(5000)
            all_pages = context.pages
            if len(all_pages) > 1:
                exam_page = all_pages[-1]
                print(f"[*] 切換到新分頁: {exam_page.url}")
                await exam_page.wait_for_load_state("domcontentloaded", timeout=15000)
                await exam_page.wait_for_timeout(3000)
            else:
                # 方法3: 可能在同一頁面導航
                exam_page = page
                await page.wait_for_timeout(3000)

        print(f"[*] 測驗頁面: {exam_page.url}")

        # 等待題目出現
        print("[*] 等待測驗題目出現 ...")
        has_exam = False
        for _ in range(20):
            try:
                has_exam = await exam_page.evaluate(
                    "() => document.querySelectorAll('.question').length > 0"
                )
                if has_exam:
                    break
            except Exception:
                await asyncio.sleep(2)
                continue
            await asyncio.sleep(2)

        if has_exam:
            await auto_answer(exam_page, all_sections)
        else:
            await exam_page.screenshot(path="exam_page.png")
            body = await exam_page.evaluate("() => document.body.innerText.substring(0, 1000)")
            print(f"[!] 未偵測到 radio，頁面內容:\n{body}")

        print("\n[*] 瀏覽器執行中，按 Ctrl+C 關閉 ...")
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            await browser.close()
            print("[*] 已關閉瀏覽器")


if __name__ == "__main__":
    asyncio.run(main())
