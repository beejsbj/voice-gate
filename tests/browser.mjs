import {chromium} from 'playwright-core';
import {spawn} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const port=Number(process.env.TEST_PORT||8876),base=`http://127.0.0.1:${port}`,token=randomBytes(32).toString('hex');
const evidence=process.env.EVIDENCE_DIR||'test-results';await mkdir(evidence,{recursive:true});
const server=spawn('.venv/bin/uvicorn',['voice_gate.api:create_app','--factory','--host','127.0.0.1','--port',String(port),'--no-access-log'],{env:{...process.env,VG_TOKENS:JSON.stringify({browser:token}),VG_PROVIDER:'fixture',VG_DATABASE:':memory:',VG_TARGETS:'[]',VG_CONFIG_FILE:''},stdio:'ignore'});
let browser;
try{
 for(let i=0;i<100;i++){try{if((await fetch(base+'/healthz')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
 browser=await chromium.launch({executablePath:process.env.CHROME_BIN||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const page=await browser.newPage({viewport:{width:1280,height:1000}}),errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.SpeechRecognition=class{constructor(){window.testSpeech=this;}start(){this.onstart?.();}stop(){this.onend?.();}abort(){this.onend?.();}};});
 await page.goto(base);await page.fill('#token',token);await page.click('#connect');await page.waitForSelector('#workspace:visible');
 assert.equal(await page.locator('#token').inputValue(),'');checks.push('Cookie login clears token input; browser workspace loads');
 await page.click('#replay');await page.waitForFunction(()=>document.querySelectorAll('#decisions .item').length===5,{},{timeout:15000});
 const labels=await page.locator('#decisions .badge').allTextContents();assert.deepEqual(labels,['command','no_action','uncertain','capture','ordinary']);
 assert.equal(await page.locator('#captures .item').count(),1);checks.push('SSE updates all five decisions and an exact retained thought');
 await page.screenshot({path:`${evidence}/desktop.png`,fullPage:true});
 await page.click('#discard');await page.waitForFunction(()=>document.querySelector('#phase').textContent==='Paused');
 await page.waitForFunction(()=>document.querySelectorAll('#captures .item').length===0);checks.push('Discard clears session captures and pauses');
 await page.click('#pause');await page.click('#speaking');await page.waitForFunction(()=>document.querySelector('#phase').textContent==='Assistant speaking');assert.equal(await page.locator('#mic').isDisabled(),true);
 await page.click('#speaking');checks.push('Assistant speaking suppresses input');
 await page.fill('#text','The weather is nice today.');await page.click('#submit');await page.waitForFunction(()=>document.querySelector('#decisions').textContent.includes('ordinary'));checks.push('Typed completed turn shares engine path');
 await page.click('#mic');await page.waitForFunction(()=>document.querySelector('#micState').textContent==='Microphone ON');
 await page.evaluate(()=>{const result=[{transcript:'Hermes, check whether you are connected.'}];result.isFinal=true;window.testSpeech.onresult({results:[result]});});
 await page.waitForTimeout(900);assert.equal(await page.locator('#decisions .item').count(),1);
 await page.click('#finish');await page.waitForFunction(()=>document.querySelectorAll('#decisions .item').length===2);checks.push('Synthetic speech final waits for end event; no physical microphone used');
 await page.click('#mic');await page.waitForFunction(()=>document.querySelector('#micState').textContent==='Microphone ON');await page.click('#pause');
 await page.evaluate(()=>{const result=[{transcript:'Hermes, check whether you are connected.'}];result.isFinal=true;window.testSpeech.onresult({results:[result]});window.testSpeech.onend();});
 await page.waitForTimeout(900);assert.equal(await page.locator('#decisions .item').count(),2);checks.push('Late speech callbacks after pause ignored');
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:`${evidence}/phone.png`,fullPage:true});checks.push('Phone layout has no horizontal overflow');
 assert.deepEqual(errors,[]);await writeFile(`${evidence}/browser.json`,JSON.stringify({checks,errors,physicalMicrophone:'not tested'},null,2));console.log(JSON.stringify({checks,errors},null,2));
}finally{if(browser)await browser.close();server.kill('SIGTERM');}
