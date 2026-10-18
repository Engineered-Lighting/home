// Actual legacy Aim inspector, shared UI and theme CSS with authored inputs.
// Producer adapters are inert. No Home graph, room, service or physical output.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const require = createRequire(import.meta.url), { chromium } = require('playwright');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), SRC = path.join(ROOT, 'app/src');
const read = name => fs.readFileSync(path.join(SRC, name), 'utf8');
const sha = value => createHash('sha256').update(value).digest('hex');
const babel = require(path.join(SRC, 'vendor/babel-7.29.0/babel.min.js'));
const aim = read('home-apartment-aim.jsx'), shared = read('home-apartment-ui.jsx'), cssSource = read('home-tokens.css');
const css = cssSource.replace(/url\("(vendor\/fonts\/[a-z-]+\.woff2)"\)/g,
  (_, name) => `url("data:font/woff2;base64,${fs.readFileSync(path.join(SRC, name)).toString('base64')}")`);
const scenarios = [
  { theme: 'dark', width: 1160, height: 900, mobile: false, legacy: true },
  { theme: 'light', width: 1160, height: 900, mobile: false, legacy: true },
  { theme: 'dark', width: 1160, height: 900, mobile: false },
  { theme: 'light', width: 1160, height: 900, mobile: false },
  { theme: 'dark', width: 390, height: 844, mobile: true },
  { theme: 'light', width: 390, height: 844, mobile: true },
];

// Resolve actual computed browser colors (including color(srgb)) through Canvas.
// Composite ancestor fills and all radial-gradient stops; inset shadows add a
// conservative background candidate. This checks rendered colors, not tokens.
function computedContrast() {
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  const rgba = value => {
    context.clearRect(0, 0, 1, 1); context.fillStyle = value; context.fillRect(0, 0, 1, 1);
    const data = context.getImageData(0, 0, 1, 1).data;
    return [data[0], data[1], data[2], data[3] / 255];
  };
  const over = (foreground, background) => foreground.slice(0, 3)
    .map((value, index) => value * foreground[3] + background[index] * (1 - foreground[3]));
  const luminance = color => color.map(value => value / 255)
    .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
    .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
  const ratio = (foreground, background) => {
    const a = luminance(over(foreground, background)), b = luminance(background);
    return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
  };
  const colors = value => (value.match(/(?:rgba?\([^)]*\)|color\([^)]*\))/g) || []).map(rgba);
  const backgrounds = element => {
    const ancestors = []; for (let node = element; node; node = node.parentElement) ancestors.unshift(node);
    let result = [[255, 255, 255]];
    for (const node of ancestors) {
      const style = getComputedStyle(node), fill = rgba(style.backgroundColor);
      result = result.map(background => over(fill, background));
      const stops = colors(style.backgroundImage);
      if (stops.length) result = result.flatMap(background => stops.map(stop => over(stop, background)));
      if (style.boxShadow.includes('inset')) {
        const shadows = colors(style.boxShadow);
        result = result.flatMap(background => [background, ...shadows.map(shadow => over(shadow, background))]);
      }
    }
    return result;
  };
  const aside = document.querySelector('[data-apt-aim-inspector]'), bounds = aside.getBoundingClientRect();
  const samples = [], walker = document.createTreeWalker(aside, NodeFilter.SHOW_TEXT);
  for (let text = walker.nextNode(); text; text = walker.nextNode()) {
    if (!text.textContent.trim()) continue;
    const element = text.parentElement, style = getComputedStyle(element);
    if (style.visibility !== 'visible' || style.display === 'none' || element.closest('option')) continue;
    const range = document.createRange(); range.selectNodeContents(text); const rect = range.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0 || rect.bottom <= bounds.top || rect.top >= bounds.bottom) continue;
    const candidates = backgrounds(element), foreground = rgba(style.color);
    samples.push({ text: text.textContent.trim().slice(0, 100), foreground: style.color,
      fontSize: parseFloat(style.fontSize), minimum: Math.min(...candidates.map(background => ratio(foreground, background))) });
  }
  for (const element of aside.querySelectorAll('input:not([type="range"]),select')) {
    const rect = element.getBoundingClientRect(); if (rect.bottom <= bounds.top || rect.top >= bounds.bottom) continue;
    const style = getComputedStyle(element), foreground = rgba(style.color);
    samples.push({ text: element.closest('label')?.textContent.trim().slice(0, 100) || element.tagName,
      foreground: style.color, fontSize: parseFloat(style.fontSize),
      minimum: Math.min(...backgrounds(element).map(background => ratio(foreground, background))) });
  }
  return samples;
}

test('actual Aim inspector theme states keep readable text, focus and bounds', { timeout: 120000 }, async () => {
  const run = path.join(ROOT, '.tmp/apartment-aim-theme-browser', 'run-' + Date.now());
  fs.mkdirSync(run, { recursive: true });
  const receipt = { schema: 'home-apartment-aim-theme-browser-1', authored_component: true, actual_aim_inspector: true,
    complete_home_app: false, room_assets: false, household_data: false, physical_commands: 0, model_saves: 0,
    camera_sessions: 0, model_sessions: 0, http_requests: [], websocket_attempts: [], errors: [], checks: [], screenshots: [],
    sources: Object.fromEntries(['home-apartment-aim.jsx', 'home-apartment-aiming.js', 'home-apartment-data.js', 'home-apartment-ui.jsx', 'home-tokens.css']
      .map(name => [name, sha(read(name))])) };
  let browser;
  try {
    const installed = fs.existsSync(chromium.executablePath());
    browser = await chromium.launch({ headless: true,
      args: ['--disable-background-networking', '--renderer-process-limit=2', '--js-flags=--max-old-space-size=512'],
      ...(!installed && process.platform === 'win32' ? { channel: 'msedge' } : {}) });
    for (const scenario of scenarios) {
      const label = `${scenario.theme}-${scenario.mobile ? 'phone' : 'desktop'}-${scenario.legacy ? 'legacy' : 'unified'}`;
      const context = await browser.newContext({ viewport: { width: scenario.width, height: scenario.height },
        hasTouch: scenario.mobile, isMobile: scenario.mobile, serviceWorkers: 'block' });
      try {
        await context.route('**/*', route => { receipt.http_requests.push(route.request().url()); return route.abort(); });
        await context.routeWebSocket('**/*', socket => { receipt.websocket_attempts.push(socket.url()); socket.close(); });
        const page = await context.newPage(); page.setDefaultTimeout(5000);
        page.on('pageerror', error => receipt.errors.push(error.message));
        await page.setContent('<meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body,#root{margin:0;width:100%;height:100%;overflow:hidden}#root{position:relative}</style><div id="root"></div>');
        await page.addStyleTag({ content: css });
        if (!scenario.legacy) await page.evaluate(() => {
          const root=document.getElementById('root');root.className='apt-home-shell';
          root.dataset.panel='controls';root.dataset.sheet='full';
          root.style.setProperty('--apt-sheet-height','calc(100% - 52px)');
          Object.assign(root.style,{display:'flex',flexDirection:'column',overflow:'hidden'});
        });
        await page.evaluate(theme => document.documentElement.dataset.theme = theme, scenario.theme);
        for (const name of ['vendor/react-18.3.1/react.production.min.js', 'vendor/react-18.3.1/react-dom.production.min.js',
          'home-apartment-aiming.js', 'home-apartment-data.js']) await page.addScriptTag({ content: read(name) });
        await page.addScriptTag({ content: babel.transform(shared, { presets: ['react'], sourceType: 'script' }).code });
        await page.addScriptTag({ content: babel.transform(aim, { presets: ['react'], sourceType: 'script' }).code });
        await page.addScriptTag({ content: `
          (()=>{
            const h=React.createElement,A=window.HomeApartmentAiming;
            const proof=window.__aimThemeProof={producerAttempts:[],pollerStarts:0,pollerStops:0};
            const forbidden=name=>(...args)=>{proof.producerAttempts.push(name);throw Error('Forbidden authored producer '+name)};
            window.HomeApartmentGimbalTelemetry={createPoller:()=>({start:()=>proof.pollerStarts++,stop:()=>proof.pollerStops++})};
            for(const name of ['callService','saveModel','adoptEngineeredFixture'])window.HomeApartmentData[name]=forbidden(name);
            Object.defineProperty(window,'__hav_haClient',{get:forbidden('HA client')});
            const fixture={...A.defaultEngineeredFixtureFields(),id:'authored-engineered',type:'light',name:'Authored ceiling fixture',
              pos:[1,1,2.4],height_preset:'ceiling',ha_entity_id:'light.authored_current'};
            fixture.spotlight={...fixture.spotlight,entity_id:'light.authored_spot'};
            fixture.radial_zones={...fixture.radial_zones,zones:fixture.radial_zones.zones.map(zone=>({...zone,entity_id:zone.number===6?null:'light.authored_radial_'+zone.number}))};
            const ordinary={id:'authored-ordinary',type:'light',name:'Authored ordinary fixture',pos:[2,2,2.4],height_preset:'ceiling',ha_entity_id:'light.authored_ordinary'};
            const model={schema_version:1,revision:1,devices:[fixture,ordinary],targets:[],zones:[]};
            const state=(value,name)=>({state:value,attributes:{friendly_name:name,brightness:128,color_temp_kelvin:3000,supported_color_modes:['color_temp'],min_color_temp_kelvin:1800,max_color_temp_kelvin:6500}});
            const states={'light.authored_current':state('on','Authored current light'),'light.authored_spot':state('on','Authored spotlight'),'light.authored_ordinary':state('off','Authored ordinary light')};
            for(let number=1;number<=5;number++)states['light.authored_radial_'+number]=state(number===5?'unavailable':number%2?'on':'off','Authored zone '+number);
            const registry={entities:Object.keys(states).map(entity_id=>({entity_id}))};
            proof.runtime={spotlight:{state:'on',brightness:128,color_temp_kelvin:3000},zones:Object.fromEntries(Array.from({length:6},(_,i)=>[i+1,{state:i%2?'off':'on',brightness:128,color_temp_kelvin:3000}])),
              current_destination:{kind:'named',id:'authored-current-target',name:'Authored current target',pos:[1,2,1],normal:[0,0,1]}};
            window.__SIM_APARTMENT_AIM_RUNTIME={read:()=>proof.runtime,readAll:()=>({[fixture.id]:proof.runtime})};
            function Proof(){
              const [fixtureId,setFixtureId]=React.useState(fixture.id);
              const [input,setInput]=React.useState({model,states,sim:false,destination:null});
              proof.update=patch=>ReactDOM.flushSync(()=>setInput(previous=>({...previous,...patch})));
              proof.states=states;
              const inspector=h(window.HomeApartmentAim,{...input,registry,connection:'offline',fixtureId,onFixtureId:setFixtureId,
                engine:null,mobile:${scenario.mobile},onDestination:forbidden('destination'),onModel:forbidden('model mutation'),
                onDirty:forbidden('dirty'),onSave:forbidden('save'),onClose:()=>{},isControlCurrent:()=>false});
              return ${scenario.legacy} ? inspector : h('aside',{className:'apt-home-panel','aria-label':'Authored workspace host'},
                h('div',{className:'apt-home-sheet-grip','aria-hidden':true}),h('nav',{className:'apt-home-tabs','aria-hidden':true}),
                h('div',{className:'apt-home-controls'},h('div',{className:'apt-ui-advanced-aim'},inspector)));
            }
            const root=ReactDOM.createRoot(document.getElementById('root'));ReactDOM.flushSync(()=>root.render(h(Proof)));
            proof.unmount=()=>ReactDOM.flushSync(()=>root.unmount());
          })();
        ` });
        await page.locator('[data-apt-aim-inspector]').waitFor();
        const resetScroll = () => page.evaluate(() => {
          document.getElementById('root').scrollTop=0;
          for(const node of document.querySelectorAll('.apt-home-controls,[data-apt-aim-inspector] *'))if(getComputedStyle(node).overflowY==='auto')node.scrollTop=0;
        });
        const visible = async locator => {
          await locator.scrollIntoViewIfNeeded();
          const result=await locator.evaluate(element=>{
            const rect=element.getBoundingClientRect();let top=Math.max(0,rect.top),bottom=Math.min(innerHeight,rect.bottom);
            for(let node=element.parentElement;node;node=node.parentElement){const style=getComputedStyle(node);if(/auto|scroll|hidden|clip/.test(style.overflowY)){const bounds=node.getBoundingClientRect();top=Math.max(top,bounds.top);bottom=Math.min(bottom,bounds.bottom)}}
            return {height:rect.height,visibleHeight:Math.max(0,bottom-top),text:element.textContent.trim()};
          });
          assert.ok(result.visibleHeight>0,'the intended screenshot state must actually be visible');
          return result;
        };
        const inspect = async (state, intended) => {
          const intendedContent=intended?await visible(intended):undefined;
          const samples = await page.evaluate(computedContrast);
          assert.ok(samples.length > 5, 'mounted state must expose actual informative text');
          for (const sample of samples) assert.ok(sample.minimum >= 4.5,
            `${label}/${state}: ${sample.text} contrast ${sample.minimum.toFixed(3)}:1 (${sample.foreground})`);
          const bounds = await page.locator('[data-apt-aim-inspector]').evaluate(element => {
            const rect=element.getBoundingClientRect();return {left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,width:innerWidth,height:innerHeight,
              overflow:element.scrollWidth>element.clientWidth,bodyOverflow:document.documentElement.scrollWidth>innerWidth};
          });
          assert.ok(bounds.left >= 0 && bounds.right <= bounds.width);
          if(scenario.legacy)assert.ok(bounds.top >= 0 && bounds.bottom <= bounds.height);
          assert.equal(bounds.overflow,false);assert.equal(bounds.bodyOverflow,false);
          const file=label+'-'+state+'.png';await page.screenshot({path:path.join(run,file)});
          receipt.screenshots.push(file);receipt.checks.push({scenario:label,state,intendedContent,bounds,samples,minimum:Math.min(...samples.map(item=>item.minimum))});
        };
        await page.getByRole('group',{name:'Lighting profile view'}).getByRole('button',{name:'Both',exact:true}).click();
        await resetScroll();await inspect('light-both',page.locator('[data-apt-current-profile]'));
        await inspect('light-engineered',page.getByText('Engineered spotlight',{exact:true}));
        await page.getByText('Details & calibration status',{exact:false}).click();
        await page.locator('details').evaluate(element=>element.scrollIntoView({block:'end'}));
        await inspect('light-details',page.locator('details > summary'));
        await page.getByRole('tab',{name:'Radials',exact:true}).click();
        const radial = page.getByRole('button',{name:'Toggle radial zone 1',exact:true});
        assert.equal(await radial.getAttribute('aria-pressed'),'true');
        assert.equal(await page.getByRole('button',{name:'Toggle radial zone 2',exact:true}).getAttribute('aria-pressed'),'false');
        assert.equal(await page.getByRole('button',{name:'Toggle radial zone 6',exact:true}).isDisabled(),true);
        await resetScroll();
        const radialVisibility=await visible(radial);
        assert.equal(radialVisibility.visibleHeight,radialVisibility.height,'the entire radial ON control must be in view');
        await inspect('radials',radial);
        await page.getByRole('tab',{name:'Setup',exact:true}).click();
        await resetScroll();await inspect('setup',page.getByText('Entity mapping',{exact:true}));
        await page.getByText('Radial orientation · floor looking upward',{exact:true}).scrollIntoViewIfNeeded();
        await inspect('setup-calibration',page.getByText('Radial orientation · floor looking upward',{exact:true}));
        await page.getByTitle('Close lighting',{exact:true}).focus();
        await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
        const focus=await page.getByTitle('Close lighting',{exact:true}).evaluate(element=>{
          const style=getComputedStyle(element);return {active:element===document.activeElement,visible:element.matches(':focus-visible'),outline:style.outlineStyle,width:parseFloat(style.outlineWidth)};
        });
        assert.equal(focus.active,true);assert.equal(focus.visible,true);assert.notEqual(focus.outline,'none');assert.ok(focus.width>=1);
        receipt.checks.push({scenario:label,state:'keyboard-focus',...focus});
        await page.getByRole('tab',{name:'Light',exact:true}).click();
        await page.evaluate(()=>window.__aimThemeProof.update({sim:true,destination:window.__aimThemeProof.runtime.current_destination}));
        await inspect('simulated-current',page.getByRole('button',{name:'Simulated aim set',exact:true}));
        await page.evaluate(()=>window.__aimThemeProof.update({sim:false,destination:null}));
        await page.getByLabel(/^fixture/).selectOption('authored-ordinary');
        await page.getByRole('button',{name:'Add Engineered profile',exact:true}).click();
        await inspect('ordinary-confirm',page.getByRole('button',{name:'Cancel',exact:true}));
        await page.getByRole('button',{name:'Cancel',exact:true}).click();
        await page.evaluate(()=>window.__aimThemeProof.update({model:{schema_version:1,revision:1,devices:[],targets:[],zones:[]}}));
        await inspect('empty',page.getByText('No ceiling fixtures are available.',{exact:true}));
        const status=await page.evaluate(()=>{const proof=window.__aimThemeProof;proof.unmount();return {attempts:proof.producerAttempts,started:proof.pollerStarts,stopped:proof.pollerStops}});
        assert.deepEqual(status.attempts,[]);assert.equal(status.started,status.stopped);
      } finally { await context.close(); }
    }
    assert.deepEqual(receipt.http_requests,[]);assert.deepEqual(receipt.websocket_attempts,[]);assert.deepEqual(receipt.errors,[]);
    receipt.passed=true;
  } catch(error) {receipt.passed=false;receipt.failure=error.stack||String(error);throw error;}
  finally {await browser?.close();fs.writeFileSync(path.join(run,'receipt.json'),JSON.stringify(receipt,null,2)+'\n');console.log(JSON.stringify({receipt:path.join(run,'receipt.json'),passed:receipt.passed,checks:receipt.checks.length,screenshots:receipt.screenshots.length}));}
});
