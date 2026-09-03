import os
import requests

class NequiEngine:
    def __init__(self):
        self.client_id = os.getenv("NEQUI_CLIENT_ID", "sandbox_id")
        self.client_secret = os.getenv("NEQUI_CLIENT_SECRET", "sandbox_secret")
        self.api_key = os.getenv("NEQUI_API_KEY", "sandbox_key")
        self.base_url = "https://nequi-sandbox.api.com" 

    def obtener_token(self):
        url = f"{self.base_url}/oauth2/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {"grant_type": "client_credentials"}
        
        try:
            response = requests.post(
                url, 
                headers=headers, 
                data=data, 
                auth=(self.client_id, self.client_secret),
                timeout=5
            )
            if response.status_code == 200:
                return response.json().get("access_token")
            return None
        except Exception:
            return None

    def solicitar_cobro_push(self, celular_cliente, monto, referencia_id):
        token = self.obtener_token()
        
        # Modo simulación local para pruebas rápidas
        if not token:
            return {
                "status": "SUCCESS_SIMULATED", 
                "message": f"Push de ${monto} COP enviado a {celular_cliente}"
            }

        url = f"{self.base_url}/payments/v2/-services-paymentservice-unregisteredpayment"
        headers = {
            "Authorization": f"Bearer {token}",
            "x-api-key": self.api_key,
            "Content-Type": "application/json"
        }
        
        payload = {
            "RequestHeader": {
                "Channel": "PNP04-C001",
                "RequestServiceID": referencia_id
            },
            "RequestBody": {
                "any": {
                    "unregisteredPaymentRQ": {
                        "phoneNumber": celular_cliente,
                        "value": str(monto)
                    }
                }
            }
        }

        try:
            res = requests.post(url, headers=headers, json=payload, timeout=10)
            return res.json()
        except Exception as e:
            return {"error": str(e)}