import {expect, test, type Page} from '@playwright/test';

// Browser-only synthetic transport fixtures. No Meta account, credential or send is used.
function snapshot(owner='owner-a', workspace='workspace-a', version='v1') {
  return {user:{id:owner,display_name:'Business fixture owner',email:`${owner}@example.invalid`},
    workspace:{id:workspace,name:'Synthetic Business fixture',timezone:'UTC',paused:false,pause_generation:0},
    workspaces:[],connections:[],conversations:[],messages:[],actions:[],drafts:[],tasks:[],jobs:[],memories:[],
    styles:[],grants:[],routes:[],activity:[],contacts:[],budget:null,retention:null,simulation:false,
    generated_at:'2026-10-07T12:00:00Z',snapshot_version:version};
}
function businessStatus(overrides:Record<string,unknown>={}) {
  return {configured:true,owner_authorized:true,webhook_configured:true,external_sends_enabled:false,
    missing_requirements:[],connectors:[{id:'business-fixture',account_id:'synthetic-business-number',status:'connected',
      lease_valid:false,capabilities:{},authorized_contacts:0,received_messages:0,delivered_messages:0,live_delivery_verified:false}],...overrides};
}
async function fixture(page:Page, options:{status?:ReturnType<typeof businessStatus>}={}) {
  let currentSnapshot:Record<string,unknown>=snapshot();
  let status=options.status??businessStatus();
  let heldStatus:Promise<void>|null=null;
  let statusCount=0;
  let messages:Record<string,unknown>[]=[];
  let heldMessages:Promise<void>|null=null;
  let messageCount=0;
  const changes:{path:string;body:Record<string,unknown>}[]=[];
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/api/auth/config')return route.fulfill({json:{backend_configured:true,google_configured:false}});
    if(path==='/api/me')return route.fulfill({json:currentSnapshot.user});
    if(path==='/api/auth/csrf')return route.fulfill({json:{csrf_token:'synthetic-browser-fixture-csrf'}});
    if(path==='/api/ui/bootstrap')return route.fulfill({json:currentSnapshot});
    if(path==='/api/integrations/whatsapp/status'){
      statusCount++;
      const response=status;const held=heldStatus;
      if(held)await held;
      return route.fulfill({json:response});
    }
    if(path==='/api/conversations/contact-fixture/messages'){
      messageCount++;const response=messages;const held=heldMessages;if(held)await held;
      return route.fulfill({json:response});
    }
    if(route.request().method()==='POST'){
      const body=route.request().postDataJSON() as Record<string,unknown>;changes.push({path,body});
      if(path==='/api/integrations/whatsapp/contacts'){
        currentSnapshot={...currentSnapshot,snapshot_version:`contact-write-${changes.length}`};
        return route.fulfill({status:201,json:{conversation:{id:'saved-contact-fixture'},permissions:{...body,share:false},history_recovered:false}});
      }
      if(path==='/api/integrations/whatsapp/owner-authorship')return route.fulfill({json:{confirmed:(body.message_ids as string[]).length,learning_requires_consent:true}});
      if(path==='/api/integrations/whatsapp/history-sync'){
        status={...status,connectors:[{...status.connectors[0],capabilities:{business_app_coexistence:'supported',business_history_request:{status:'uncertain'}}}]};
        return route.fulfill({json:{status:'uncertain',resubmitted:false,history_sharing_verified:false}});
      }
    }
    return route.fulfill({status:404,json:{detail:'Unavailable synthetic fixture route'}});
  });
  return {changes,setSnapshot:(value:Record<string,unknown>)=>{currentSnapshot=value;},setStatus:(value:ReturnType<typeof businessStatus>)=>{status=value;},
    setMessages:(value:Record<string,unknown>[])=>{messages=value;},holdStatus:(value:Promise<void>|null)=>{heldStatus=value;},statusCount:()=>statusCount,
    holdMessages:(value:Promise<void>|null)=>{heldMessages=value;},messageCount:()=>messageCount};
}
const connection=(page:Page)=>page.getByRole('region',{name:'WhatsApp Business connection',exact:true});

test('Business setup explains missing server requirements without a credential input or false connection',async({page})=>{
  await fixture(page,{status:businessStatus({configured:false,owner_authorized:false,webhook_configured:false,connectors:[],
    missing_requirements:['authorized_google_owner','business_phone_number','business_access_token','webhook_app_secret','webhook_verify_token']})});
  await page.goto('/connections');const panel=connection(page);
  await expect(panel.getByText('Business setup needs attention',{exact:true})).toBeVisible();
  await expect(panel.getByRole('button',{name:'Set up my Business number',exact:true})).toBeDisabled();
  await expect(panel.getByText('The deployment operator must authorize your Google account for this Business number.',{exact:true})).toBeVisible();
  await expect(panel.getByText('Provider credentials are entered in the deployment settings, never in this app.',{exact:true})).toBeVisible();
  await expect(panel.getByRole('textbox')).toHaveCount(0);
  await expect(panel).toContainText('Phone linking is configured separately in the linked-device pilot. Live group access is unavailable.');
});

test('each contact starts with zero grants and sending requires explicit recipient opt-in',async({page})=>{
  const data=await fixture(page);await page.goto('/connections');const panel=connection(page);
  await expect(panel.getByText('Business identity needs verification',{exact:true})).toBeVisible();
  await expect(panel.getByText('Live message delivery has not been verified. Identity verification alone does not prove replies are working.',{exact:true})).toBeVisible();
  const form=panel.getByRole('form',{name:'Authorize an individual WhatsApp contact',exact:true});
  for(const choice of await form.getByRole('checkbox').all())await expect(choice).not.toBeChecked();
  await form.getByRole('textbox',{name:'Contact name',exact:true}).fill('Synthetic contact');
  await form.getByRole('textbox',{name:'Full international WhatsApp number',exact:true}).fill('+15555550123');
  await form.getByRole('button',{name:'Save this contact’s scope',exact:true}).click();
  await expect.poll(()=>data.changes.length).toBe(1);
  expect(data.changes[0]).toEqual({path:'/api/integrations/whatsapp/contacts',body:{connector_id:'business-fixture',phone_number:'+15555550123',title:'Synthetic contact',
    read:false,retain:false,learn:false,draft:false,send:false,recipient_opted_in:false}});
  await expect(form.getByRole('textbox',{name:'Contact name',exact:true})).toHaveValue('');
  await form.getByRole('textbox',{name:'Contact name',exact:true}).fill('Synthetic opted-in contact');
  await form.getByRole('textbox',{name:'Full international WhatsApp number',exact:true}).fill('+15555550124');
  await form.getByRole('checkbox',{name:'Permit sending to this contact, subject to my rules',exact:true}).check();
  await expect(form.getByRole('button',{name:'Save this contact’s scope',exact:true})).toBeDisabled();
  await form.getByRole('checkbox',{name:'Read new messages from this contact',exact:true}).check();
  await form.getByRole('checkbox',{name:'Retain this contact’s messages under my retention policy',exact:true}).check();
  await expect(form.getByRole('button',{name:'Save this contact’s scope',exact:true})).toBeDisabled();
  await form.getByRole('checkbox',{name:'This contact has opted in to receive Business messages',exact:true}).check();
  await form.getByRole('button',{name:'Save this contact’s scope',exact:true}).click();
  await expect.poll(()=>data.changes.length).toBe(2);
  expect(data.changes[1].body).toMatchObject({read:true,retain:true,learn:false,draft:false,send:true,recipient_opted_in:true});
  expect(data.changes.some(row=>row.path.includes('/automation/') || row.path.includes('/dispatch') || row.path.includes('/actions'))).toBe(false);
});

test('writing review selects only attested owner examples and excludes assistant and incoming messages',async({page})=>{
  const data=await fixture(page);
  data.setSnapshot({...snapshot(),conversations:[{id:'contact-fixture',connector_id:'business-fixture',provider_chat_id:'15555550123',title:'Synthetic review contact',kind:'contact',control_state:'DRAFT_MODE',revision:1}]});
  data.setMessages([
    {id:'owner-one',text:'My first personal reply.',direction:'outbound',author_kind:'unknown_owner_outgoing'},
    {id:'owner-two',text:'A different operator may have written this.',direction:'outbound',author_kind:'unknown_owner_outgoing'},
    {id:'assistant-one',text:'Assistant output must never become an owner example.',direction:'outbound',author_kind:'assistant'},
    {id:'incoming-one',text:'Incoming message is not owner writing.',direction:'inbound',author_kind:'contact_human'},
  ]);
  await page.goto('/connections');const panel=connection(page);
  await panel.getByRole('combobox',{name:'Contact for writing review',exact:true}).selectOption('contact-fixture');
  await panel.getByRole('button',{name:'Load reviewable messages',exact:true}).click();
  await expect(panel.getByText('My first personal reply.',{exact:true})).toBeVisible();
  await expect(panel.getByText('Assistant output must never become an owner example.',{exact:true})).toHaveCount(0);
  await expect(panel.getByText('Incoming message is not owner writing.',{exact:true})).toHaveCount(0);
  const confirm=panel.getByRole('button',{name:'Confirm only my selected messages',exact:true});await expect(confirm).toBeDisabled();
  await panel.getByRole('checkbox',{name:'I personally wrote this message: My first personal reply.',exact:true}).check();
  await confirm.click();await expect.poll(()=>data.changes.length).toBe(1);
  expect(data.changes[0]).toEqual({path:'/api/integrations/whatsapp/owner-authorship',body:{conversation_id:'contact-fixture',message_ids:['owner-one'],confirm_authored_by_owner:true}});
});

test('eligible history requires prior sharing consent and an uncertain request cannot be repeated',async({page})=>{
  const data=await fixture(page,{status:businessStatus({connectors:[{id:'business-fixture',account_id:'synthetic-business-number',status:'connected',lease_valid:true,
    capabilities:{business_app_coexistence:'supported'},authorized_contacts:1,received_messages:0,delivered_messages:0,live_delivery_verified:false}]})});
  await page.goto('/connections');const panel=connection(page);
  const submit=panel.getByRole('button',{name:'Request eligible Business history',exact:true});await expect(submit).toBeDisabled();
  await panel.getByRole('checkbox',{name:'I enabled history sharing during approved Business app onboarding.',exact:true}).check();
  await submit.click();await expect.poll(()=>data.changes.length).toBe(1);
  expect(data.changes[0]).toEqual({path:'/api/integrations/whatsapp/history-sync',body:{connector_id:'business-fixture'}});
  await expect(panel.getByText('The original history request has an unresolved outcome. Review its provider status with your deployment operator before taking further action.',{exact:true})).toBeVisible();
  await expect(panel.getByRole('button',{name:'Original history request recorded',exact:true})).toBeDisabled();
  await panel.getByRole('button',{name:'Refresh connection status',exact:true}).click();
  expect(data.changes.filter(row=>row.path.endsWith('/history-sync'))).toHaveLength(1);
});

test('an expired provider lease cannot enable a history request even when an older status says verified',async({page})=>{
  const data=await fixture(page,{status:businessStatus({connectors:[{id:'business-fixture',account_id:'synthetic-business-number',status:'connected',lease_valid:true,
    lease_expires_at:'2020-01-01T00:00:00Z',capabilities:{business_app_coexistence:'supported'},authorized_contacts:1,received_messages:0,delivered_messages:0,live_delivery_verified:false}]})});
  await page.goto('/connections');const panel=connection(page);
  await expect(panel.getByText('Business identity needs verification',{exact:true})).toBeVisible();
  await panel.getByRole('checkbox',{name:'I enabled history sharing during approved Business app onboarding.',exact:true}).check();
  await expect(panel.getByRole('button',{name:'Request eligible Business history',exact:true})).toBeDisabled();
  expect(data.changes).toEqual([]);
});

test('an owner switch clears contact input and discards an older in-flight private status',async({page})=>{
  const data=await fixture(page);await page.goto('/connections');const panel=connection(page);
  await panel.getByRole('textbox',{name:'Contact name',exact:true}).fill('Private contact input for owner A');
  const previousCount=data.statusCount();
  let release!:()=>void;const held=new Promise<void>(resolve=>{release=resolve;});data.holdStatus(held);
  await panel.getByRole('button',{name:'Refresh connection status',exact:true}).click();await expect.poll(()=>data.statusCount()).toBeGreaterThan(previousCount);
  data.setSnapshot(snapshot('owner-b','workspace-b','v2'));
  data.setStatus(businessStatus({configured:false,owner_authorized:false,connectors:[],missing_requirements:['authorized_google_owner']}));data.holdStatus(null);
  const refreshed=page.waitForResponse(response=>response.url().includes('/api/ui/bootstrap'));
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await refreshed;
  await expect(panel.getByText('Business setup needs attention',{exact:true})).toBeVisible();
  await expect(panel.getByRole('textbox')).toHaveCount(0);
  const oldCompletion=page.waitForResponse(response=>response.url().includes('/integrations/whatsapp/status?workspace_id=workspace-a'));
  release();await oldCompletion;
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
  await expect(panel.getByRole('textbox')).toHaveCount(0);
  await expect(panel.getByText('Business identity needs verification',{exact:true})).toHaveCount(0);
  await expect(panel.getByText('Private contact input for owner A',{exact:true})).toHaveCount(0);
});

test('revoking the selected contact drops a late writing-review response before private text can appear',async({page})=>{
  const data=await fixture(page);
  data.setSnapshot({...snapshot(),conversations:[{id:'contact-fixture',connector_id:'business-fixture',provider_chat_id:'15555550123',title:'Synthetic revocation contact',kind:'contact',control_state:'DRAFT_MODE',revision:1}]});
  data.setMessages([{id:'owner-revoked',text:'Synthetic private writing revoked while loading.',direction:'outbound',author_kind:'unknown_owner_outgoing'}]);
  let release!:()=>void;const held=new Promise<void>(resolve=>{release=resolve;});data.holdMessages(held);
  await page.goto('/connections');const panel=connection(page);
  await panel.getByRole('combobox',{name:'Contact for writing review',exact:true}).selectOption('contact-fixture');
  await panel.getByRole('button',{name:'Load reviewable messages',exact:true}).click();await expect.poll(()=>data.messageCount()).toBe(1);
  data.setSnapshot(snapshot('owner-a','workspace-a','permission-revoked'));
  const refreshed=page.waitForResponse(response=>response.url().includes('/api/ui/bootstrap'));
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await refreshed;
  await expect(panel.getByRole('combobox',{name:'Contact for writing review',exact:true})).toHaveCount(0);
  const completed=page.waitForResponse(response=>response.url().includes('/conversations/contact-fixture/messages'));
  release();await completed;
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
  await expect(panel.getByText('Synthetic private writing revoked while loading.',{exact:true})).toHaveCount(0);
  await expect(panel.getByRole('button',{name:'Confirm only my selected messages',exact:true})).toHaveCount(0);
  expect(data.changes).toEqual([]);
});
