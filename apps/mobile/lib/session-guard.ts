/** Native session transitions share one ordered secure-storage queue. */
export class SessionGuard {
  epoch=0;
  readSequence=0;
  private storage=Promise.resolve();
  invalidate(){this.epoch++;this.readSequence++;}
  invalidateReads(){this.readSequence++;}
  matches(epoch:number,sequence?:number){return this.epoch===epoch&&(sequence===undefined||this.readSequence===sequence);}
  enqueueStorage(operation:()=>Promise<void>,expectedEpoch?:number):Promise<void>{
    const result=this.storage.catch(()=>{}).then(async()=>{
      if(expectedEpoch!==undefined&&!this.matches(expectedEpoch))return;
      await operation();
    });
    this.storage=result;
    return result;
  }
}
export function draftScopeKey(ownerId:string,workspaceId:string,conversationId:string,recipientId:string){
  return [ownerId,workspaceId,conversationId,recipientId].map(encodeURIComponent).join('|');
}
