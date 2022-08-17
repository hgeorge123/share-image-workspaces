import boto3
import time
import json
#Habilitar y utilizar al reestructurar como *lambda_handler*
#import logging

#logger = logging.getLogger()
#logger.setLevel(logging.INFO)

#Cargar Configuración desde JSON
config_file = open('aws_share_image.json')
config_json = json.load(config_file)


#Sleep Time
sleep_time = config_json["sleep_time"]
#Region 
region = config_json["region"]
#ACCOUNTS
accounts = config_json["accounts"]
#Images
images = config_json["images"]
#Reintentos Actualización Bundle Id
max_retries = config_json["max_retries"]
#Sleep Al Reintentar
retry_sleep = config_json["retry_sleep"]

dest_bundle_names = ["Cencosud-Standard_Base","Cencosud-Power_Base","Cencosud-Performance_Base"]

#Conexión Cuenta Origen
source_client = boto3.client('workspaces', region_name=region)

copied_images=[]

def dest_workspaces(arn, session_name, region):

    #Obtener Credenciales Usando STS 
    #Rol STS (Cuenta Destino)
    sts_connection = boto3.client('sts', region_name=region)
    cross_account_role = sts_connection.assume_role(
        RoleArn=arn,
        RoleSessionName=session_name
    )
    ACCESS_KEY = cross_account_role['Credentials']['AccessKeyId']
    SECRET_KEY = cross_account_role['Credentials']['SecretAccessKey']
    SESSION_TOKEN = cross_account_role['Credentials']['SessionToken']

    #Cuenta Destino (STS)
    sts_connection = boto3.client(
        'sts',
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        aws_session_token=SESSION_TOKEN,
        region_name=region
    )
    
    get_account_id = sts_connection.get_caller_identity()
    destination_account = get_account_id['Account']    
    #print("Cuenta Destino: ",destination_account)

    #Cliente Boto3 En Cuenta Destino (Workspaces)
    dest_client = boto3.client(
        'workspaces',
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        aws_session_token=SESSION_TOKEN,
        region_name=region
    )

    return destination_account, dest_client



for account in accounts:

    arn = "arn:aws:iam::{account_number}:role/workspaces-admin".format(account_number=account)
    session_name = "{account_number}-workspaces-admin".format(account_number=account)
    
    destination_account, dest_client = dest_workspaces(arn, session_name, region)

    print("Cuenta Destino: ",destination_account)
    
    for img in images:
    
        image_id = img["image_id"]
        image_tags = img["tags"]
        
        #Obtener Imagen Necesaria
        images = source_client.describe_workspace_images(
            ImageIds=[image_id],
        )

        for image in images['Images']:

            print("Procesando Imagen: ",image)

            #Validar Estatus de la Imagen en Origen
            if(image["State"] != "AVAILABLE"):
                print("Saltando Imagen: ", image['ImageId'], " - Estatus Actual: ",  image["State"])
                continue

            #Consultar Permisos de la Imagen.   
            response_sip = source_client.describe_workspace_image_permissions(
                ImageId=image['ImageId']
            )
            print("Respuesta *describe_workspace_image_permissions* en Origen: ", response_sip)
            
            compartida = False
            for sip in response_sip["ImagePermissions"]:
                if sip["SharedAccountId"]==destination_account:
                    print("Saltando Imagen: ", image['ImageId'], " - Ya se encuentra compartida con: ", destination_account)
                    compartida = True
                    break
            
            if compartida:
                continue    
            #Actualizar Permisos de la Imagen
            response_suip = source_client.update_workspace_image_permission(
                ImageId = image['ImageId'],
                AllowCopyImage = True,
                SharedAccountId = destination_account
            )    

            print("Respuesta *update_workspace_image_permission* en Origen: ", response_suip)
            
            #Consultar Permisos de la Imagen En Destino?
            response_dip = dest_client.describe_workspace_image_permissions(
                ImageId=image['ImageId']
            )
            #Se valida si esta disponible pata copiar????
            
            print("Respuesta *describe_workspace_image_permissions* en Destino: ", response_dip)
            
            #Copiar Imagen
            try:
                response_dci = dest_client.copy_workspace_image(
                    Name=image['Name'],
                    Description=image['Description'],
                    SourceImageId=image['ImageId'],   
                    SourceRegion=region,
                    Tags=image_tags        
                )

                print("Respuesta *copy_workspace_image* en Destino: ", response_dci)                    
                #Mejorar Captura de Errores

                if (response_dci["ImageId"]):
                    copied_images.append({"arn":arn, "session_name":session_name, "image_id":response_dci["ImageId"]})
                
            except:
                pass

tries = 0
continuar = True  
if len(copied_images) > 0:      
    print("Esperando ", str(sleep_time) , " segundos mientras copia.")
    time.sleep(sleep_time)

while (continuar and len(copied_images) > 0):

    if tries > 0:
        print("Esperando ", str(retry_sleep) , " segundos para reintentar (" , tries  , ")")    
        time.sleep(retry_sleep)
            
    tries = tries + 1  

    print("Actualizando el Bundle de ", len(copied_images) , " Imagenes")
    
    copied_images2 = []
    for copied_image in copied_images:
    
        destination_account, dest_client = dest_workspaces(copied_image["arn"], copied_image["session_name"], region)    
        # Codificar flujo de espera por si no está disponible la copia
        #Mover el flujo de asignacion de bundleid fuera del for original
        response_dwb = dest_client.describe_workspace_bundles()

        print("Respuesta *describe_workspace_bundles* en Destino: ",response_dwb)

        for dest_bundle_name in dest_bundle_names:
            dest_bundle_id = ""
            for db in response_dwb["Bundles"]:
            
                if  db["Name"] == dest_bundle_name:
                    dest_bundle_id = db["BundleId"]
                    print("BundleId de Destino Ubicado: ", dest_bundle_id )
                    break

            if (dest_bundle_id != ""):
            
                try:

                    response_uwb = dest_client.update_workspace_bundle(
                        BundleId=dest_bundle_id,
                        ImageId=copied_image["image_id"]
                    )     
                    
                    print("Respuesta *update_workspace_bundle* en Destino: ", response_uwb)       
                    
                except:
                    if copied_image not in copied_images2:
                        #Reintentar Imagen Por Error
                        print("Error Actualuzando BundleID. Agregando a lista de reintentos")                    
                        copied_images2.append(copied_image)         
            else:
                print("Error: No Se ubico  ID de Bundle en Destino. Deberiamos Crearlo Automaticamente Aqui?")        
                continue

    copied_images = copied_images2              
    if (tries >= max_retries):
        continuar = False
        
print("Fin")
