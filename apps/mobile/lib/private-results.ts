import type {MiloSnapshot} from '@milo/contracts';

export type AuthorizedSnapshot=MiloSnapshot&{snapshot_version?:string};
export type PrivateResult<T>={value:T;version:string};

/** A changed authorized snapshot invalidates cached private command/evidence text. */
export function snapshotVersion(snapshot:AuthorizedSnapshot|null):string {
  if(!snapshot)return 'signed-out';
  if(snapshot.snapshot_version)return `${snapshot.user.id}:${snapshot.workspace.id}:${snapshot.snapshot_version}`;
  const {generated_at:_generated,pagination:_pagination,...privateView}=snapshot;
  return `${snapshot.user.id}:${snapshot.workspace.id}:${JSON.stringify(privateView)}`;
}
/** Never relabel an old private response with a newly authorized generation. */
export function reconcilePrivateResponse<T>(value:T,before:AuthorizedSnapshot|null,fresh:AuthorizedSnapshot|null):T|null {
  if(!before||!fresh)return null;
  if(snapshotVersion(before)===snapshotVersion(fresh))return value;
  if(value&&typeof value==='object'&&'id' in value&&typeof value.id==='string'){
    const current=[...fresh.conversations,...fresh.actions,...fresh.memories,...fresh.styles,...fresh.drafts,
      ...fresh.tasks,...fresh.jobs,...fresh.connections,...fresh.contacts,...fresh.routes,...fresh.grants].find(row=>row.id===value.id);
    // Return the newly authorized object, never text/evidence from the old response.
    return current as T??null;
  }
  return null;
}
export function currentPrivateResult<T>(entry:PrivateResult<T>|null,snapshot:AuthorizedSnapshot|null):T|null {
  return entry&&entry.version===snapshotVersion(snapshot)?entry.value:null;
}
