import React, {useEffect, useRef, useState} from 'react';
import {Switch, View} from 'react-native';
import {Body, Button, Card, Field, Heading, styles} from './ui';
import {useMilo} from '../lib/state';
import {snapshotVersion, type PrivateResult} from '../lib/private-results';
import type {Message} from '@milo/contracts';

type BusinessConnector = {
  id:string; account_id:string; status:string; lease_valid:boolean; capabilities:Record<string,unknown>;
  lease_expires_at?:string|null;
  authorized_contacts:number; received_messages:number; delivered_messages:number; live_delivery_verified:boolean;
};
type BusinessStatus = {configured:boolean; owner_authorized:boolean; webhook_configured:boolean;
  external_sends_enabled:boolean; missing_requirements:string[]; connectors:BusinessConnector[]};
type Consent = {read:boolean; retain:boolean; learn:boolean; draft:boolean; send:boolean; recipient_opted_in:boolean};
const emptyConsent = ():Consent => ({read:false,retain:false,learn:false,draft:false,send:false,recipient_opted_in:false});
const choices:[keyof Consent,string][] = [
  ['read','Read new messages from this contact'], ['retain','Retain this contact’s messages under my retention policy'],
  ['learn','Learn this contact’s voice and evidence-linked context'], ['draft','Prepare unsent replies for my review'],
  ['send','Permit sending to this contact, subject to my rules'], ['recipient_opted_in','This contact has opted in to receive Business messages'],
];
const requirements:Record<string,string> = {
  authorized_google_owner:'The deployment operator must authorize your Google account for this Business number.',
  business_phone_number:'The deployment operator must configure an eligible WhatsApp Business number.',
  business_access_token:'The deployment operator must connect the Business account securely on the server.',
  webhook_app_secret:'The deployment operator must enable signed incoming WhatsApp events.',
  webhook_verify_token:'The deployment operator must register the public WhatsApp webhook with Meta.',
};

/** No provider credential, fake pairing code, or implicit contact grant enters the app. */
export function WhatsAppConnection() {
  const app = useMilo();
  const workspaceId = app.snapshot?.workspace.id ?? '';
  const version = `${app.mode}:${snapshotVersion(app.snapshot)}`;
  const latest = useRef(version); latest.current = version;
  const appRef = useRef(app); appRef.current = app;
  const active = useRef(true);
  const flight = useRef(false);
  const statusSequence = useRef(0);
  const [statusEntry,setStatusEntry] = useState<PrivateResult<BusinessStatus>|null>(null);
  const [errorEntry,setErrorEntry] = useState<PrivateResult<string>|null>(null);
  const [loading,setLoading] = useState(true);
  const [busy,setBusy] = useState(false);
  const [notice,setNotice] = useState('');
  const [connectorId,setConnectorId] = useState('');
  const [phone,setPhone] = useState('');
  const [title,setTitle] = useState('');
  const [consent,setConsent] = useState<Consent>(emptyConsent);
  const [historyConsent,setHistoryConsent] = useState(false);
  const [reviewChatId,setReviewChatId] = useState('');
  const [reviewEntry,setReviewEntry] = useState<PrivateResult<Message[]> & {chatId:string}|null>(null);
  const [selection,setSelection] = useState<{key:string;ids:string[]}>({key:'',ids:[]});
  const status = statusEntry?.version === version ? statusEntry.value : null;
  const error = errorEntry?.version === version ? errorEntry.value : null;
  const connector = status?.connectors.find(row=>row.id===connectorId) ?? status?.connectors[0];
  const authorized = Boolean(status?.owner_authorized && status.configured);
  const prerequisites = !(consent.learn || consent.draft || consent.send) || (consent.read && consent.retain);
  const validConsent = prerequisites && (!consent.send || consent.recipient_opted_in);
  const currentLease=Boolean(connector?.lease_valid && (!connector.lease_expires_at || Date.parse(connector.lease_expires_at)>Date.now()));
  const historyEligible = Boolean(connector?.status==='connected' && currentLease &&
    connector.capabilities.business_app_coexistence==='supported' && status?.webhook_configured && connector.authorized_contacts>0);
  const priorHistoryRequest=connector?.capabilities.business_history_request;
  const historyRequestStatus=priorHistoryRequest && typeof priorHistoryRequest==='object' && 'status' in priorHistoryRequest?String(priorHistoryRequest.status):'';
  const reviewKey=`${version}:${reviewChatId}`;
  const currentReview=useRef(reviewKey);currentReview.current=reviewKey;
  const examples=reviewEntry?.chatId===reviewChatId && reviewEntry.version===version?reviewEntry.value:null;
  const selectedIds=selection.key===reviewKey?selection.ids:[];
  const reviewChats=app.snapshot?.conversations.filter(chat=>chat.connector_id===connector?.id && chat.kind==='contact')??[];

  useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
  useEffect(()=>{
    if(app.mode!=='live' || !workspaceId){setLoading(false);return;}
    let valid=true;
    const load=()=>{
      const sequence=++statusSequence.current;
      const current=()=>valid && active.current && latest.current===version && sequence===statusSequence.current;
      setLoading(true);
      void appRef.current.request<BusinessStatus>(`/v1/integrations/whatsapp/status?workspace_id=${encodeURIComponent(workspaceId)}`)
        .then(value=>{if(current()){setStatusEntry({value,version});setErrorEntry(null);}})
        .catch(failure=>{if(current())setErrorEntry({value:failure instanceof Error?failure.message:'Business connection status is unavailable. Try Refresh connection status.',version});})
        .finally(()=>{if(current())setLoading(false);});
    };
    load();
    return()=>{valid=false;};
    // The provider refreshes on foreground and every 30s. Load status after its
    // acknowledged snapshot, avoiding a parallel read that its SessionGuard invalidates.
  },[app.mode,workspaceId,version,app.lastUpdated]);

  async function refreshStatus(){
    if(flight.current || app.mode!=='live')return;
    const started=version;const sequence=++statusSequence.current;setLoading(true);setErrorEntry(null);
    try {const value=await app.request<BusinessStatus>(`/v1/integrations/whatsapp/status?workspace_id=${encodeURIComponent(workspaceId)}`);
      if(active.current && latest.current===started && sequence===statusSequence.current)setStatusEntry({value,version:started});}
    catch(failure){if(active.current && latest.current===started && sequence===statusSequence.current)setErrorEntry({value:failure instanceof Error?failure.message:'Connection status was not confirmed.',version:started});}
    finally{if(active.current && latest.current===started && sequence===statusSequence.current)setLoading(false);}
  }

  async function change(path:string,body?:unknown,onSaved?:()=>void){
    if(flight.current || !app.online || !authorized)return;
    const started=version;statusSequence.current++;flight.current=true;setBusy(true);setLoading(false);setErrorEntry(null);setNotice('');
    try{
      await app.request(`/v1${path}`,{method:'POST',body});
      if(!active.current || latest.current!==started)return;
      onSaved?.();setNotice('Request acknowledged. Review the refreshed connection evidence. No automatic reply was enabled.');
      const fresh=await app.refresh();
      if(!fresh && active.current && latest.current===started)throw new Error('The request returned, but current scope was not confirmed. Refresh before reviewing or retrying.');
      if(active.current && latest.current===started){
        const value=await app.request<BusinessStatus>(`/v1/integrations/whatsapp/status?workspace_id=${encodeURIComponent(workspaceId)}`);
        if(active.current && latest.current===started)setStatusEntry({value,version:started});}
    }catch(failure){if(active.current && latest.current===started)setErrorEntry({value:failure instanceof Error?failure.message:'The change was not confirmed. Review current status before explicitly retrying.',version:started});}
    finally{flight.current=false;if(active.current)setBusy(false);}
  }

  function saveContact(){
    if(!connector || !validConsent || !phone.trim() || !title.trim())return;
    void change('/integrations/whatsapp/contacts',{connector_id:connector.id,phone_number:phone.trim(),title:title.trim(),...consent},()=>{
      setPhone('');setTitle('');setConsent(emptyConsent());});
  }

  async function loadReview(){
    if(flight.current || !reviewChatId || !authorized || !app.online)return;
    const started=reviewKey;flight.current=true;setBusy(true);setReviewEntry(null);setSelection({key:started,ids:[]});setErrorEntry(null);
    try{const rows=await app.request<Message[]>(`/v1/conversations/${encodeURIComponent(reviewChatId)}/messages?limit=30`);
      if(active.current && currentReview.current===started)setReviewEntry({value:rows.filter(row=>row.direction==='outbound' && row.author_kind==='unknown_owner_outgoing' && row.deleted!==true && row.text.trim()),version,chatId:reviewChatId});}
    catch(failure){if(active.current && currentReview.current===started)setErrorEntry({value:failure instanceof Error?failure.message:'Message examples are unavailable in this contact’s current scope.',version});}
    finally{flight.current=false;if(active.current)setBusy(false);}
  }

  if(app.mode!=='live')return <Card><Heading>Connect WhatsApp Business</Heading><Body>You are exploring a synthetic demo. Sign in with Google to set up your eligible Business number and choose each contact’s permissions.</Body></Card>;
  if(!workspaceId)return null;
  return <>
    <Card><Heading>Connect WhatsApp Business</Heading><Body>Connect an eligible Business number, then choose individual contacts. Personal WhatsApp linking and live group access are unavailable. Connecting does not grant access to every chat.</Body>
      <Button secondary label="Refresh connection status" disabled={busy || loading} onPress={()=>void refreshStatus()}/>
      {loading && <Body small>Checking current Business connection…</Body>}
      {error && <View accessibilityLiveRegion="assertive"><Body>{error}</Body></View>}
      {notice && <View accessibilityLiveRegion="polite"><Body>{notice}</Body></View>}
      {status && <>
        {!!status.missing_requirements.length && <><Heading>Business setup needs attention</Heading>{status.missing_requirements.map(item=><Body key={item} small>{requirements[item]??'The deployment operator must complete the remaining Business setup.'}</Body>)}<Body small>Provider credentials are entered in deployment settings, never in this app.</Body></>}
        <Body small>Incoming events: {status.webhook_configured?'Webhook configured · live receipt still required':'Webhook setup required'}</Body>
        <Body small>External sending: {status.external_sends_enabled?'Enabled on the server · contact rules still apply':'Disabled on the server'}</Body>
        {!connector && <Button label={busy?'Connecting…':'Set up my Business number'} disabled={!authorized || busy || !app.online} onPress={()=>void change('/integrations/whatsapp/connect',{workspace_id:workspaceId})}/>}
        {connector && <>
          {status.connectors.length>1 && status.connectors.map(row=><Button key={row.id} secondary label={`${connector.id===row.id?'Selected · ':''}${row.account_id}`} disabled={busy} onPress={()=>{setConnectorId(row.id);setPhone('');setTitle('');setConsent(emptyConsent());setHistoryConsent(false);setReviewChatId('');setReviewEntry(null);setNotice('');}}/>)}
          <Heading>{connector.status==='connected' && currentLease?'Business identity verified at last check':'Business identity needs verification'}</Heading>
          <Body small>{connector.authorized_contacts} contacts with read and retain permission · {connector.received_messages} incoming messages observed · {connector.delivered_messages} deliveries confirmed.</Body>
          <Body small>{connector.live_delivery_verified?'A provider delivery receipt has been observed. Each new message still needs its own receipt.':'Live message delivery has not been verified. Identity verification alone does not prove replies are working.'}</Body>
          <Button secondary label="Verify Business number" disabled={!authorized || busy || !app.online} onPress={()=>void change(`/connectors/${encodeURIComponent(connector.id)}/verify`)}/>
        </>}
      </>}
    </Card>
    {connector && <>
      <Card><Heading>Choose one contact’s scope</Heading><Body small>Saving replaces this contact’s permission choices. Every choice starts off. Reading and retention are required for learning, drafting or sending.</Body>
        <Field label="Contact name" value={title} maxLength={160} editable={!busy && authorized} onChangeText={setTitle}/>
        <Field label="Full international WhatsApp number" value={phone} maxLength={40} keyboardType="phone-pad" autoComplete="off" editable={!busy && authorized} placeholder="Country code and phone number" onChangeText={setPhone}/>
        {choices.map(([key,label])=><View key={key} style={[styles.row,{minHeight:48,flexWrap:'nowrap'}]}><Switch accessibilityLabel={label} value={consent[key]} disabled={busy || !authorized} onValueChange={value=>setConsent(previous=>({...previous,[key]:value}))}/><View style={{flex:1}}><Body small>{label}</Body></View></View>)}
        {!prerequisites && <Body small>Choose read and retain before enabling learning, drafts or sends.</Body>}{consent.send && !consent.recipient_opted_in && <Body small>Recipient opt-in is required for sending.</Body>}
        <Body small>Sending permission creates no Auto rule or message. Sharing between contacts remains off. Earlier messages are not imported by adding a contact.</Body>
        <Button label={busy?'Saving contact scope…':'Save this contact’s scope'} disabled={!authorized || busy || !app.online || !validConsent || !phone.trim() || !title.trim()} onPress={saveContact}/>
      </Card>
      <Card><Heading>Optional eligible Business app history</Heading><Body>Approved Meta Coexistence onboarding and provider history-sharing permission can recover eligible direct-chat history, up to 180 days. Group history is unsupported. Select read and retain permissions for each intended contact first.</Body>
        <Body small>Only request this during the eligible onboarding window. A request acknowledgement does not prove history arrived; the original request is never blindly repeated.</Body>
        {historyRequestStatus?<Body>{historyRequestStatus==='accepted'?'Meta accepted the original history request. History arrival and completeness still need verification.':historyRequestStatus==='failed'?'The original history request was rejected. Review the eligible setup with your deployment operator; no request is automatically repeated.':'The original history request has an unresolved outcome. Review its provider status with your deployment operator before taking further action.'}</Body>:<View style={[styles.row,{minHeight:48,flexWrap:'nowrap'}]}><Switch accessibilityLabel="I enabled history sharing during approved Business app onboarding." value={historyConsent} disabled={busy} onValueChange={setHistoryConsent}/><View style={{flex:1}}><Body small>I enabled history sharing during approved Business app onboarding.</Body></View></View>}
        <Button secondary label={historyRequestStatus?'Original history request recorded':'Request eligible Business history'} disabled={!authorized || !historyEligible || !historyConsent || !!historyRequestStatus || busy || !app.online} onPress={()=>void change('/integrations/whatsapp/history-sync',{connector_id:connector.id})}/>
      </Card>
      {!!reviewChats.length && <Card><Heading>Review your own writing examples</Heading><Body small>Business messages may be written by several people. Select only messages you personally wrote. Confirmation grants no learning permission; learning remains a separate choice for this contact.</Body>
        {reviewChats.map(chat=><Button key={chat.id} secondary label={`${reviewChatId===chat.id?'Selected · ':''}${chat.title}`} disabled={busy} onPress={()=>{setReviewChatId(chat.id);setReviewEntry(null);setSelection({key:'',ids:[]});}}/>)}
        <Button secondary label="Load reviewable messages" disabled={!reviewChatId || !reviewChats.some(chat=>chat.id===reviewChatId) || !authorized || busy || !app.online} onPress={()=>void loadReview()}/>
        {examples && <><Body small>Personally authored examples · latest 30 messages only</Body>{examples.length?examples.map(message=><View key={message.id} style={[styles.row,{minHeight:48,flexWrap:'nowrap'}]}><Switch accessibilityLabel={`I personally wrote this message: ${message.text}`} value={selectedIds.includes(message.id)} disabled={busy} onValueChange={checked=>setSelection({key:reviewKey,ids:checked?[...selectedIds,message.id]:selectedIds.filter(id=>id!==message.id)})}/><View style={{flex:1}}><Body small>I personally wrote this message:</Body><Body>{message.text}</Body></View></View>):<Body small>No unreviewed outgoing examples are available in this selected batch. This does not prove all history has been reviewed.</Body>}</>}
        {!!examples?.length && <Button label="Confirm only my selected messages" disabled={!selectedIds.length || !authorized || busy || !app.online} onPress={()=>void change('/integrations/whatsapp/owner-authorship',{conversation_id:reviewChatId,message_ids:selectedIds,confirm_authored_by_owner:true},()=>{setReviewEntry(null);setSelection({key:'',ids:[]});})}/>}
      </Card>}
    </>}
  </>;
}
