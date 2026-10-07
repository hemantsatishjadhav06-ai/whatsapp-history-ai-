import type {MiloSnapshot} from './domain';

export type OwnerAnswerContext = {
  schema_version:1;owner_id:string;workspace_id:string;conversation_id:string;connector_id:string;
  conversation_revision:number;control_epoch:number;control_state:string;permission_version:number;
  permissions:Record<'read'|'retain'|'learn'|'draft'|'send'|'share',boolean>;
  permission_expires_at:string|null;pause_generation:number;connector_fence:number;connector_status:string;
  memory_versions:Record<string,[number,number,string|null]>;expires_at:string;
};
export type OwnerAnswer = {conversation_id:string;audience:'owner_only';external_actions:false;
  text:string;evidence_message_ids:string[];missing_facts:string[];authorization_context:OwnerAnswerContext};
const permissionNames=['read','retain','learn','draft','send','share'] as const;
function record(value:unknown):value is Record<string,unknown>{return !!value&&typeof value==='object'&&!Array.isArray(value);}
function instant(value:unknown):number|null {return typeof value==='string'&&Number.isFinite(Date.parse(value))?Date.parse(value):null;}
function sameExpiry(left:unknown,right:unknown):boolean {
  return left===null&&right===null||instant(left)!==null&&instant(left)===instant(right);
}
export function isOwnerAnswer(value:unknown):value is OwnerAnswer {
  return record(value)&&value.audience==='owner_only'&&value.external_actions===false&&'authorization_context' in value;
}
export function ownerAnswerExpiresAt(value:unknown):number|null {
  return isOwnerAnswer(value)&&record(value.authorization_context)?instant(value.authorization_context.expires_at):null;
}
/** A budget receipt cannot invalidate a still-current exact-chat answer.
 *
 * Only server-generated owner answers use this narrower fence. Ordinary private
 * results still require the complete authorized snapshot generation. Real message
 * and Forget APIs change conversation_revision; silent source/memory expiry is
 * bounded by the answer's server-issued expires_at (at most five minutes).
 */
export function ownerAnswerIsCurrent(value:unknown,snapshot:MiloSnapshot|null,now=Date.now()):value is OwnerAnswer {
  if(!snapshot||!isOwnerAnswer(value)||!record(value.authorization_context))return false;
  const context=value.authorization_context;
  if(context.schema_version!==1||context.owner_id!==snapshot.user.id||context.workspace_id!==snapshot.workspace.id
    ||value.conversation_id!==context.conversation_id||snapshot.workspace.paused
    ||context.pause_generation!==snapshot.workspace.pause_generation||!record(context.permissions)||context.permissions.read!==true
    ||!record(context.memory_versions)||Object.keys(context.memory_versions).length>20
    ||(ownerAnswerExpiresAt(value)??0)<=now)return false;
  const chat=snapshot.conversations.find(row=>row.id===context.conversation_id);
  const connection=snapshot.connections.find(row=>row.id===context.connector_id);
  if(!chat||!connection||chat.connector_id!==context.connector_id||chat.revision!==context.conversation_revision
    ||chat.control_epoch!==context.control_epoch||chat.control_state!==context.control_state
    ||connection.fence!==context.connector_fence||connection.status!==context.connector_status
    ||!record(chat.permissions)||chat.permissions.version!==context.permission_version
    ||!sameExpiry(chat.permissions.expires_at,context.permission_expires_at))return false;
  if(context.permission_expires_at!==null&&(instant(context.permission_expires_at)??0)<=now)return false;
  const permissions=chat.permissions;
  if(permissionNames.some(name=>typeof context.permissions[name]!=='boolean'||permissions[name]!==context.permissions[name]))return false;
  for(const [id,version] of Object.entries(context.memory_versions)){
    if(!Array.isArray(version)||version.length!==3||!Number.isInteger(version[0])||!Number.isInteger(version[1]))return false;
    const memory=snapshot.memories.find(row=>row.id===id);
    if(!memory||memory.conversation_id!==context.conversation_id||memory.status!=='confirmed'
      ||memory.version!==version[0]||memory.suppression_version!==version[1]||!sameExpiry(memory.expires_at,version[2])
      ||version[2]!==null&&(instant(version[2])??0)<=now)return false;
  }
  return true;
}
