"""
AWS CDK stack for the Trade Store service.

Provisions for both staging and prod:
  - VPC with public / private subnets
  - RDS PostgreSQL 17 (single-AZ for staging, multi-AZ for prod)
  - MSK Kafka 3.7 cluster
  - ECS Fargate: API service (behind ALB) + Kafka consumer service
  - Auto-scaling on the API service (prod only)

Runtime configuration injected through CDK context keys:
  certificate_arn – ACM certificate ARN for the HTTPS listener (optional)

Image source: built and pushed by the stack via DockerImageAsset (repo root
Dockerfile). Run cdk deploy from repo root or infra/cdk; Docker must be available.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_msk as msk
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import custom_resources as cr
from constructs import Construct


@dataclasses.dataclass
class TradeStoreConfig:
    environment: str
    vpc_cidr: str
    rds_instance_type: ec2.InstanceType
    multi_az: bool
    kafka_instance_type: str
    kafka_broker_count: int
    api_cpu: int
    api_memory: int
    consumer_cpu: int
    consumer_memory: int
    api_desired_count: int
    consumer_desired_count: int
    enable_autoscaling: bool = False
    autoscaling_min: int = 1
    autoscaling_max: int = 4


class TradeStoreStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: TradeStoreConfig,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        env = config.environment

        # ── VPC ───────────────────────────────────────────────────────────────
        vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr(config.vpc_cidr),
            max_azs=3 if env == "prod" else 2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
        )

        # ── Security Groups ────────────────────────────────────────────────────
        alb_sg = ec2.SecurityGroup(self, "AlbSg", vpc=vpc, description=f"ALB - {env}")
        alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS inbound")
        alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP redirect")

        api_sg = ec2.SecurityGroup(
            self, "ApiSg", vpc=vpc, description=f"API ECS - {env}"
        )
        api_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(8000), "From ALB")

        consumer_sg = ec2.SecurityGroup(
            self, "ConsumerSg", vpc=vpc, description=f"Consumer ECS - {env}"
        )

        rds_sg = ec2.SecurityGroup(self, "RdsSg", vpc=vpc, description=f"RDS - {env}")
        rds_sg.add_ingress_rule(api_sg, ec2.Port.tcp(5432), "From API")
        rds_sg.add_ingress_rule(consumer_sg, ec2.Port.tcp(5432), "From Consumer")

        msk_sg = ec2.SecurityGroup(self, "MskSg", vpc=vpc, description=f"MSK - {env}")
        for sg in (api_sg, consumer_sg):
            msk_sg.add_ingress_rule(sg, ec2.Port.tcp(9092), "Kafka plaintext")
            msk_sg.add_ingress_rule(sg, ec2.Port.tcp(9094), "Kafka TLS")

        # ── RDS PostgreSQL 17 ─────────────────────────────────────────────────
        db_secret = secretsmanager.Secret(
            self,
            "DbSecret",
            secret_name=f"trade-store/{env}/db-credentials",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username":"trade_user"}',
                generate_string_key="password",
                exclude_punctuation=True,
            ),
        )

        db = rds.DatabaseInstance(
            self,
            "Rds",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_17_4,
            ),
            instance_type=config.rds_instance_type,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[rds_sg],
            multi_az=config.multi_az,
            database_name="trade_store",
            credentials=rds.Credentials.from_secret(db_secret),
            storage_encrypted=True,
            deletion_protection=(env == "prod"),
            removal_policy=(
                RemovalPolicy.RETAIN if env == "prod" else RemovalPolicy.DESTROY
            ),
        )

        # ── MSK Kafka 3.7 ─────────────────────────────────────────────────────
        private_subnets = vpc.select_subnets(
            subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
        )
        msk_cluster = msk.CfnCluster(
            self,
            "Msk",
            cluster_name=f"trade-store-{env}",
            kafka_version="3.7.x",
            number_of_broker_nodes=config.kafka_broker_count,
            broker_node_group_info=msk.CfnCluster.BrokerNodeGroupInfoProperty(
                instance_type=config.kafka_instance_type,
                client_subnets=private_subnets.subnet_ids[: config.kafka_broker_count],
                security_groups=[msk_sg.security_group_id],
                storage_info=msk.CfnCluster.StorageInfoProperty(
                    ebs_storage_info=msk.CfnCluster.EBSStorageInfoProperty(
                        volume_size=100,
                    ),
                ),
            ),
            encryption_info=msk.CfnCluster.EncryptionInfoProperty(
                encryption_in_transit=msk.CfnCluster.EncryptionInTransitProperty(
                    client_broker="TLS",
                    in_cluster=True,
                ),
            ),
        )

        # ── DynamoDB — Request lifecycle table ────────────────────────────────
        request_table = dynamodb.Table(
            self,
            "RequestTable",
            table_name=f"trade-requests-{env}",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            point_in_time_recovery=True,
            removal_policy=(
                RemovalPolicy.RETAIN if env == "prod" else RemovalPolicy.DESTROY
            ),
        )

        # ── ECS Cluster ───────────────────────────────────────────────────────
        cluster = ecs.Cluster(
            self,
            "EcsCluster",
            vpc=vpc,
            cluster_name=f"trade-store-{env}",
            container_insights=True,
        )

        ecr.Repository(
            self,
            "EcrRepo",
            repository_name="trade-store",
            removal_policy=RemovalPolicy.RETAIN,
        )

        repo_root = Path(__file__).resolve().parents[3]
        app_image = ecr_assets.DockerImageAsset(
            self,
            "AppImage",
            directory=str(repo_root),
            file="Dockerfile",
            exclude=[".git", "**/cdk.out", "**/.git"],
        )
        container_image = ecs.ContainerImage.from_docker_image_asset(app_image)
        certificate_arn: str | None = self.node.try_get_context("certificate_arn")

        # Kafka bootstrap servers TLS endpoint: AWS::MSK::Cluster no longer returns it via GetAtt,
        # so we call the GetBootstrapBrokers API via a custom resource.
        msk_bootstrap_cr = cr.AwsCustomResource(
            self,
            "MskBootstrapBrokers",
            on_create=cr.AwsSdkCall(
                service="Kafka",
                action="getBootstrapBrokers",
                parameters={"ClusterArn": msk_cluster.attr_arn},
                physical_resource_id=cr.PhysicalResourceId.of("MskBootstrapBrokers"),
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements(
                [
                    iam.PolicyStatement(
                        actions=["kafka:GetBootstrapBrokers"],
                        resources=[msk_cluster.attr_arn],
                    )
                ]
            ),
        )
        msk_bootstrap_cr.node.add_dependency(msk_cluster)
        kafka_bootstrap = msk_bootstrap_cr.get_response_field(
            "BootstrapBrokerStringTls"
        )

        shared_env = {
            "DB_HOST": db.db_instance_endpoint_address,
            "DB_PORT": "5432",
            "DB_NAME": "trade_store",
            "DB_USER": "trade_user",
            "KAFKA_BOOTSTRAP_SERVERS": kafka_bootstrap,
            "DYNAMODB_TABLE_NAME": request_table.table_name,
            "AWS_REGION": self.region,
            "ENVIRONMENT": env,
        }
        shared_secrets = {
            "DB_PASSWORD": ecs.Secret.from_secrets_manager(db_secret, "password"),
        }

        # ── API Fargate Task ──────────────────────────────────────────────────
        api_task = ecs.FargateTaskDefinition(
            self,
            "ApiTask",
            cpu=config.api_cpu,
            memory_limit_mib=config.api_memory,
        )
        api_task.add_container(
            "api",
            image=container_image,
            command=["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
            environment=shared_env,
            secrets=shared_secrets,
            port_mappings=[ecs.PortMapping(container_port=8000)],
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="api",
                log_group=logs.LogGroup(
                    self,
                    "ApiLogs",
                    log_group_name=f"/ecs/trade-store-{env}/api",
                    retention=logs.RetentionDays.ONE_MONTH,
                    removal_policy=RemovalPolicy.RETAIN,
                ),
            ),
        )
        db_secret.grant_read(api_task.task_role)
        request_table.grant_read_write_data(api_task.task_role)

        api_service = ecs.FargateService(
            self,
            "ApiService",
            cluster=cluster,
            task_definition=api_task,
            desired_count=config.api_desired_count,
            security_groups=[api_sg],
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            assign_public_ip=False,
            min_healthy_percent=50 if env == "prod" else 0,
            max_healthy_percent=200,
        )

        # ── Consumer Fargate Task ─────────────────────────────────────────────
        consumer_task = ecs.FargateTaskDefinition(
            self,
            "ConsumerTask",
            cpu=config.consumer_cpu,
            memory_limit_mib=config.consumer_memory,
        )
        consumer_task.add_container(
            "consumer",
            image=container_image,
            command=["python", "-m", "app.kafka.consumer"],
            environment=shared_env,
            secrets=shared_secrets,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="consumer",
                log_group=logs.LogGroup(
                    self,
                    "ConsumerLogs",
                    log_group_name=f"/ecs/trade-store-{env}/consumer",
                    retention=logs.RetentionDays.ONE_MONTH,
                    removal_policy=RemovalPolicy.RETAIN,
                ),
            ),
        )
        db_secret.grant_read(consumer_task.task_role)
        request_table.grant_read_write_data(consumer_task.task_role)

        consumer_service = ecs.FargateService(
            self,
            "ConsumerService",
            cluster=cluster,
            task_definition=consumer_task,
            desired_count=config.consumer_desired_count,
            security_groups=[consumer_sg],
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            assign_public_ip=False,
        )

        # ── ALB ───────────────────────────────────────────────────────────────
        alb = elbv2.ApplicationLoadBalancer(
            self,
            "Alb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        if certificate_arn:
            # Redirect HTTP → HTTPS, then add TLS listener
            alb.add_redirect()
            listener = alb.add_listener(
                "Https",
                port=443,
                certificates=[elbv2.ListenerCertificate.from_arn(certificate_arn)],
            )
        else:
            # No cert provided (e.g. early staging): plain HTTP listener
            listener = alb.add_listener("Http", port=80, open=True)

        listener.add_targets(
            "ApiTarget",
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[api_service],
            health_check=elbv2.HealthCheck(
                path="/api/v1/health",
                healthy_http_codes="200",
            ),
        )

        if config.enable_autoscaling:
            scaling = api_service.auto_scale_task_count(
                min_capacity=config.autoscaling_min,
                max_capacity=config.autoscaling_max,
            )
            scaling.scale_on_cpu_utilization(
                "CpuScaling",
                target_utilization_percent=70,
                scale_in_cooldown=Duration.seconds(60),
                scale_out_cooldown=Duration.seconds(60),
            )

        # ── Stack outputs ─────────────────────────────────────────────────────
        CfnOutput(
            self,
            "AlbDnsName",
            value=alb.load_balancer_dns_name,
            description="ALB DNS name",
        )
        CfnOutput(
            self,
            "DbEndpoint",
            value=db.db_instance_endpoint_address,
            description="RDS endpoint",
        )
        CfnOutput(
            self,
            "EcsClusterArn",
            value=cluster.cluster_arn,
            description="ECS cluster ARN",
        )
        CfnOutput(
            self,
            "KafkaBootstrap",
            value=kafka_bootstrap,
            description="MSK TLS bootstrap brokers",
        )
